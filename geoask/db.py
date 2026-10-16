"""Read-only DuckDB access with spatial support.

Everything the agent can run goes through `run_sql`, which enforces:
  - single statement, SELECT/WITH only (no DDL/DML, no ATTACH, no COPY)
  - a hard row cap
  - a wall-clock budget
Geometry columns are returned as GeoJSON so callers can map them directly.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "geoask.duckdb"
MAX_ROWS = 2000
QUERY_TIMEOUT_S = 20

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|export|import|install|load|pragma|set|call)\b",
    re.IGNORECASE,
)


class QueryError(ValueError):
    """Raised when a query is rejected or fails."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    geometry_columns: list[str] = field(default_factory=list)

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows[:n]]


class GeoDB:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"database not found: {self.path} (run etl/build_db.py)")
        self._con = duckdb.connect(str(self.path), read_only=True)
        self._con.execute("LOAD spatial;")
        # Sandbox: no file/URL access from SQL (read_text, read_csv, glob, COPY...), and freeze
        # settings so a query can't turn it back on. The keyword filter in validate() is a
        # second layer; this is the one that actually holds.
        # Connections to the same file share one DuckDB instance, so only the first locks it.
        if not self._con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]:
            self._con.execute("SET enable_external_access = false;")
            self._con.execute("SET lock_configuration = true;")
        assert self._con.execute("SELECT current_setting('enable_external_access')").fetchone()[0] is False
        self._lock = threading.Lock()

    # -- schema --------------------------------------------------------------

    def schema_text(self) -> str:
        """Human/LLM-readable schema with column types and row counts."""
        tables = [r[0] for r in self._con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1"
        ).fetchall()]
        out = []
        for t in tables:
            cols = self._con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position", [t]
            ).fetchall()
            n = self._con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            out.append(f"TABLE {t}  ({n:,} rows)")
            out.extend(f"  {c}: {d}" for c, d in cols)
        return "\n".join(out)

    # -- queries -------------------------------------------------------------

    @staticmethod
    def validate(sql: str) -> str:
        s = sql.strip().rstrip(";").strip()
        if not s:
            raise QueryError("empty query")
        if ";" in s:
            raise QueryError("only a single statement is allowed")
        if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
            raise QueryError("only SELECT / WITH queries are allowed")
        if _FORBIDDEN.search(s):
            raise QueryError("query contains a forbidden keyword (read-only access)")
        return s

    def run_sql(self, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
        s = self.validate(sql)
        with self._lock:
            con = self._con.cursor()
            timer = threading.Timer(QUERY_TIMEOUT_S, con.interrupt)
            timer.start()
            try:
                rel = con.execute(f"SELECT * FROM ({s}) LIMIT {max_rows + 1}")
                columns = [d[0] for d in rel.description]
                types = [d[1] for d in rel.description]
                rows = rel.fetchall()
            except duckdb.InterruptException as e:
                raise QueryError(f"query exceeded {QUERY_TIMEOUT_S}s budget") from e
            except duckdb.Error as e:
                raise QueryError(str(e).splitlines()[0]) from e
            finally:
                timer.cancel()
                con.close()

        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        geom_cols = [c for c, t in zip(columns, types) if str(t).upper() == "GEOMETRY"]
        if geom_cols:
            rows = self._geoms_to_geojson(s, columns, geom_cols, max_rows)
        return QueryResult(
            sql=s, columns=columns, rows=[list(r) for r in rows],
            row_count=len(rows), truncated=truncated, geometry_columns=geom_cols,
        )

    def _geoms_to_geojson(self, s: str, columns: list[str], geom_cols: list[str], max_rows: int):
        """Re-run with geometry columns serialized as GeoJSON dicts."""
        select = ", ".join(
            f'ST_AsGeoJSON("{c}") AS "{c}"' if c in geom_cols else f'"{c}"' for c in columns
        )
        with self._lock:
            rows = self._con.execute(f"SELECT {select} FROM ({s}) LIMIT {max_rows}").fetchall()
        gi = [columns.index(c) for c in geom_cols]
        out = []
        for r in rows:
            r = list(r)
            for i in gi:
                r[i] = json.loads(r[i]) if r[i] else None
            out.append(r)
        return out

    def to_geojson(self, result: QueryResult, geometry_column: str | None = None) -> dict:
        """Turn a QueryResult into a GeoJSON FeatureCollection.

        Uses the named geometry column, else the first geometry column, else
        latitude/longitude columns if present.
        """
        cols = result.columns
        gcol = geometry_column or (result.geometry_columns[0] if result.geometry_columns else None)
        features = []
        if gcol:
            gi = cols.index(gcol)
            for r in result.rows:
                if r[gi] is None:
                    continue
                props = {c: v for c, v in zip(cols, r) if c != gcol}
                features.append({"type": "Feature", "geometry": r[gi], "properties": _jsonable(props)})
        else:
            lat = _find(cols, ("latitude", "lat", "y"))
            lon = _find(cols, ("longitude", "lon", "lng", "x"))
            if lat is None or lon is None:
                raise QueryError("result has no geometry column and no latitude/longitude columns")
            li, oi = cols.index(lat), cols.index(lon)
            for r in result.rows:
                if r[li] is None or r[oi] is None:
                    continue
                props = {c: v for i, (c, v) in enumerate(zip(cols, r)) if i not in (li, oi)}
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(r[oi]), float(r[li])]},
                    "properties": _jsonable(props),
                })
        return {"type": "FeatureCollection", "features": features}


def _find(cols: list[str], names: tuple[str, ...]) -> str | None:
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out
