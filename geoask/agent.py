"""The GeoAsk agent: a tool-using loop over the read-only spatial database.

The agent gets three tools:
  get_schema  - table/column listing (cheap, call first when unsure)
  run_sql     - read-only DuckDB SQL, returns a row preview
  show_on_map - run a SQL and render the result as a map layer

Every tool call is recorded as a Step so the UI can show exactly what was
run, how many rows came back, and where each number in the answer came from.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import anthropic

from .db import GeoDB, QueryError
from .raster import LandCover

MODEL = "claude-opus-5-5"
MAX_TURNS = 12

SYSTEM_PROMPT = """You are GeoAsk, an analyst that answers questions about geospatial data by writing SQL against a read-only DuckDB database with the spatial extension loaded.

How to work:
- Call get_schema first if you are not sure about table or column names.
- Write SQL with run_sql. Prefer one well-formed query over many small ones.
- When the question is about "where" or has a natural map answer, call show_on_map with a query that returns either a `geom` column or `latitude`/`longitude` columns plus a few descriptive columns (name, operator, count...). Keep map layers under ~1500 rows; aggregate or filter if needed.
- Every number in your final answer must come from a query you ran in this conversation. Never estimate or recall from memory.
- If the data cannot answer the question, say so plainly and say what is missing.

Spatial SQL notes (DuckDB spatial):
- Points are stored in `geom` (GEOMETRY, lon/lat WGS84). Many data centers have no coordinates: filter on `has_coords` for spatial work and say how many rows were excluded if it matters.
- Distance in meters between two points: ST_Distance_Sphere(a.geom, b.geom).
- Point-in-polygon: ST_Within(point, polygon). `alpr_cities.bbox_geom` is a city bounding box (a rough proxy for the city, not its exact boundary; say so when you use it).
- Hex binning: ST_Hex is not available; use ROUND(latitude, 1), ROUND(longitude, 1) grid cells instead.
- Use state_code (2-letter) for US states; the raw `state` column is messy.
- `operator` is the cleaned company name; `company` is raw.

Data notes:
- datacenters: 18,110 facilities worldwide from the Global Data Center Map (Ringmast4r). Coordinates vary in precision (building, city, or country centroid).
- alpr_cities: 2,401 US cities with automated license plate reader camera counts by vendor (Finding Flock / OpenStreetMap). `population` (Census 2023 estimate) and `cameras_per_10k_residents` are present for incorporated places; NULL for census-designated places, so filter on population IS NOT NULL for per-capita questions and say how many cities were excluded.
- datacenter_landcover: for the ~60 data centers inside the NOAA C-CAP 2021 land-cover tile (Washington DC, Reston/Herndon VA, central Maryland; Ashburn and Sterling fall just outside its western edge), the share of developed, forest, wetland, water and agricultural land within 500 m. Join on datacenter_id = datacenters.id.
- land_cover_near: samples the same raster at any point inside that tile. Use it for "what surrounds this site" questions; outside the tile it returns an error, so say the data only covers the DC area.

Answer style: lead with the direct answer, then the key numbers, then one line of caveats. Be concise. Do not paste SQL into the answer; the UI already shows it."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": "List all tables and columns in the database with row counts.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "run_sql",
        "description": "Run a read-only SQL query (SELECT/WITH) against the DuckDB spatial database. Returns the first rows and the total row count (capped at 2000).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT or WITH statement."},
                "purpose": {"type": "string", "description": "One short sentence: what this query is for."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "show_on_map",
        "description": "Run a read-only SQL query and render its rows as a map layer. The query must return a `geom` column or `latitude` and `longitude` columns, plus descriptive columns for the popup. Keep it under ~1500 rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "layer_name": {"type": "string", "description": "Short legend label, e.g. 'AWS sites near Microsoft'."},
                "style": {
                    "type": "string",
                    "enum": ["point", "polygon", "heat"],
                    "description": "point for markers, polygon for areas, heat for dense point clouds.",
                },
            },
            "required": ["sql", "layer_name", "style"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "land_cover_near",
        "description": "Sample NOAA C-CAP 2021 land cover (30 m raster) within a radius of a point and return the share of each class (developed, forest, wetland, water...). Only covers the Washington DC / Northern Virginia / central Maryland tile (about 77.5W-76.5W, 38.5N-39.2N); other points return an error.",
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "radius_m": {"type": "number", "description": "Radius in meters, 30-5000. Default 500."},
                "label": {"type": "string", "description": "What this point is, e.g. 'Equinix DC2, Ashburn'."},
            },
            "required": ["latitude", "longitude", "radius_m", "label"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class Step:
    tool: str
    input: dict[str, Any]
    ok: bool
    row_count: int | None = None
    truncated: bool = False
    columns: list[str] = field(default_factory=list)
    preview: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0


@dataclass
class Layer:
    name: str
    style: str
    geojson: dict[str, Any]
    sql: str


@dataclass
class Answer:
    question: str
    text: str
    steps: list[Step]
    layers: list[Layer]
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_ms: int
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GeoAskAgent:
    def __init__(self, db: GeoDB, client: anthropic.Anthropic | None = None, model: str = MODEL,
                 effort: str = "medium", landcover: LandCover | None = None):
        self.db = db
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        if landcover is None:
            try:
                landcover = LandCover()
            except FileNotFoundError:
                landcover = None
        self.landcover = landcover

    def _tool_land_cover_near(self, inp: dict, steps: list[Step], layers: list[Layer]) -> str:
        t0 = time.perf_counter()
        if self.landcover is None:
            raise QueryError("land cover raster is not available in this deployment")
        try:
            s = self.landcover.summary(float(inp["latitude"]), float(inp["longitude"]), float(inp["radius_m"]))
        except ValueError as e:
            steps.append(Step(tool="land_cover_near", input=inp, ok=False, error=str(e),
                              elapsed_ms=int((time.perf_counter() - t0) * 1000)))
            raise QueryError(str(e)) from e
        preview = [{"group": k, "pct": v} for k, v in s["groups"].items()]
        steps.append(Step(tool="land_cover_near", input=inp, ok=True, row_count=len(s["classes"]),
                          columns=["group", "pct"], preview=preview,
                          elapsed_ms=int((time.perf_counter() - t0) * 1000)))
        layers.append(Layer(
            name=inp["label"], style="point", sql="",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                "properties": {"label": inp["label"], "radius_m": s["radius_m"],
                               **{f"{k} %": v for k, v in s["groups"].items()}},
            }]},
        ))
        return json.dumps(s)

    # -- tool implementations -----------------------------------------------

    def _tool_get_schema(self, _: dict, steps: list[Step], layers: list[Layer]) -> str:
        text = self.db.schema_text()
        steps.append(Step(tool="get_schema", input={}, ok=True))
        return text

    def _tool_run_sql(self, inp: dict, steps: list[Step], layers: list[Layer]) -> str:
        t0 = time.perf_counter()
        try:
            res = self.db.run_sql(inp["sql"])
        except QueryError as e:
            steps.append(Step(tool="run_sql", input=inp, ok=False, error=str(e),
                              elapsed_ms=int((time.perf_counter() - t0) * 1000)))
            raise
        steps.append(Step(
            tool="run_sql", input=inp, ok=True, row_count=res.row_count, truncated=res.truncated,
            columns=res.columns, preview=_jsonable_rows(res.preview(10)),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        ))
        payload = {
            "row_count": res.row_count,
            "truncated_at_2000": res.truncated,
            "columns": res.columns,
            "rows": _jsonable_rows(res.preview(50)),
        }
        return json.dumps(payload, default=str)

    def _tool_show_on_map(self, inp: dict, steps: list[Step], layers: list[Layer]) -> str:
        t0 = time.perf_counter()
        try:
            res = self.db.run_sql(inp["sql"], max_rows=1500)
            gj = self.db.to_geojson(res)
        except QueryError as e:
            steps.append(Step(tool="show_on_map", input=inp, ok=False, error=str(e),
                              elapsed_ms=int((time.perf_counter() - t0) * 1000)))
            raise
        layers.append(Layer(name=inp["layer_name"], style=inp["style"], geojson=gj, sql=res.sql))
        steps.append(Step(
            tool="show_on_map", input=inp, ok=True, row_count=len(gj["features"]),
            truncated=res.truncated, columns=res.columns, preview=_jsonable_rows(res.preview(5)),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        ))
        return json.dumps({"rendered_features": len(gj["features"]), "layer": inp["layer_name"]})

    # -- main loop -----------------------------------------------------------

    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        steps: list[Step] = []
        layers: list[Layer] = []
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        in_tok = out_tok = 0
        handlers = {
            "get_schema": self._tool_get_schema,
            "run_sql": self._tool_run_sql,
            "show_on_map": self._tool_show_on_map,
            "land_cover_near": self._tool_land_cover_near,
        }

        response = None
        for _ in range(MAX_TURNS):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            in_tok += response.usage.input_tokens + (response.usage.cache_read_input_tokens or 0)
            out_tok += response.usage.output_tokens

            if response.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                handler = handlers.get(block.name)
                try:
                    if handler is None:
                        raise QueryError(f"unknown tool {block.name}")
                    content = handler(dict(block.input), steps, layers)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                except QueryError as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})

        text = "".join(b.text for b in response.content if b.type == "text") if response else ""
        if response is not None and response.stop_reason == "refusal":
            text = text or "The model declined to answer this question."
        return Answer(
            question=question, text=text.strip(), steps=steps, layers=layers,
            model=response.model if response else self.model,
            input_tokens=in_tok, output_tokens=out_tok,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            stop_reason=response.stop_reason if response else "no_response",
        )


def _jsonable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append({k: (v if isinstance(v, (str, int, float, bool, dict, list)) or v is None else str(v))
                    for k, v in r.items()})
    return out
