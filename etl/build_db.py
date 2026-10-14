"""Build the GeoAsk DuckDB database from raw source files.

Sources:
  - Global-Data-Center-Map (Ringmast4r)  -> datacenters
  - Finding Flock ALPR cameras by city    -> alpr_cities

Usage:
  python etl/build_db.py --raw <dir with datacenters.db and findingflock-cameras-by-city.geojson> --out data/geoask.duckdb
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico",
}
NAME_TO_ABBR = {v.lower(): k for k, v in US_STATES.items()}

# Common operator aliases collapsed into one canonical brand.
OPERATOR_ALIASES = {
    r"^amazon( aws| web services)?$": "Amazon AWS",
    r"^aws$": "Amazon AWS",
    r"^microsoft( azure)?$": "Microsoft",
    r"^digital realty( trust)?$": "Digital Realty",
    r"^cyrusone( data centers)?$": "CyrusOne",
    r"^stack infrastructure$": "Stack Infrastructure",
    r"^ntt( global data centers| ltd\.?| communications)?$": "NTT",
    r"^google( cloud)?$": "Google",
    r"^equinix.*$": "Equinix",
    r"^centurylink$": "Lumen",
    r"^(meta|meta, inc\.|facebook)$": "Meta",
    r"^oracle( cloud infrastructure)?$": "Oracle",
    r"^apple( inc\.)?$": "Apple",
}
# "DC 11 DE LLC (Amazon)" style: a shell company with the real operator in parentheses.
_PAREN_BRAND = re.compile(r"\(([^()]+)\)\s*$")


def normalize_state(state: str | None, country: str | None) -> str | None:
    """Return a 2-letter US state code, or None when unknown / not US."""
    if not isinstance(state, str) or not state or country != "United States":
        return None
    s = state.strip()
    if s.upper() in US_STATES:
        return s.upper()
    return NAME_TO_ABBR.get(s.lower())


def normalize_operator(company: str | None) -> str | None:
    if not isinstance(company, str) or not company.strip():
        return None
    c = company.strip()
    m = _PAREN_BRAND.search(c)
    if m and len(m.group(1).split()) <= 3:
        c = m.group(1).strip()
    low = c.lower()
    for pattern, canon in OPERATOR_ALIASES.items():
        if re.match(pattern, low):
            return canon
    return c


def load_datacenters(sqlite_path: Path) -> pd.DataFrame:
    con = sqlite3.connect(sqlite_path)
    df = pd.read_sql_query("SELECT * FROM datacenters", con)
    con.close()

    df["country"] = df["country"].replace({"": None, "Unknown": None})
    df["state_code"] = [normalize_state(s, c) for s, c in zip(df["state"], df["country"])]
    df["operator"] = df["company"].map(normalize_operator)
    df["has_coords"] = df["latitude"].notna() & df["longitude"].notna()
    # Drop obviously bad coordinates.
    bad = df["has_coords"] & ~(df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180))
    df.loc[bad, ["latitude", "longitude"]] = None
    df.loc[bad, "has_coords"] = False
    return df[["id", "name", "company", "operator", "city", "state", "state_code",
               "country", "address", "latitude", "longitude", "has_coords"]]


def load_alpr(geojson_path: Path) -> pd.DataFrame:
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    rows = []
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        v = p.get("vendors") or {}
        rows.append({
            "geoid": p["geoid"], "city": p["city"], "place_name": p["place_name"],
            "state_code": p["state"], "state_name": p["state_name"],
            "cameras": p["cameras"], "land_sq_mi": p["land_sq_mi"],
            "cameras_per_sq_mi": p["cameras_per_sq_mi"],
            "flock": v.get("flock", 0), "motorola": v.get("motorola", 0),
            "genetec": v.get("genetec", 0), "leonardo": v.get("leonardo", 0),
            "neology": v.get("neology", 0), "other": v.get("other", 0),
            "unknown": v.get("unknown", 0),
            "bbox_w": p["bbox"][0], "bbox_s": p["bbox"][1],
            "bbox_e": p["bbox"][2], "bbox_n": p["bbox"][3],
            "latitude": lat, "longitude": lon, "source_url": p["url"],
        })
    return pd.DataFrame(rows)


def build(raw: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    dc = load_datacenters(raw / "datacenters.db")
    alpr = load_alpr(raw / "findingflock-cameras-by-city.geojson")

    con = duckdb.connect(str(out))
    con.execute("INSTALL spatial; LOAD spatial;")
    con.register("dc_df", dc)
    con.register("alpr_df", alpr)
    con.execute("""
        CREATE TABLE datacenters AS
        SELECT *, CASE WHEN has_coords THEN ST_Point(longitude, latitude) END AS geom
        FROM dc_df
    """)
    con.execute("""
        CREATE TABLE alpr_cities AS
        SELECT *, ST_Point(longitude, latitude) AS geom,
               ST_MakeEnvelope(bbox_w, bbox_s, bbox_e, bbox_n) AS bbox_geom
        FROM alpr_df
    """)
    con.execute("""
        CREATE TABLE us_states AS
        SELECT * FROM (VALUES %s) AS t(state_code, state_name)
    """ % ",".join(f"('{k}','{v}')" for k, v in US_STATES.items()))

    n_dc = con.execute("SELECT count(*), sum(has_coords::int) FROM datacenters").fetchone()
    n_alpr = con.execute("SELECT count(*) FROM alpr_cities").fetchone()[0]
    con.close()
    print(f"datacenters: {n_dc[0]} rows, {n_dc[1]} with coordinates")
    print(f"alpr_cities: {n_alpr} rows")
    print(f"wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data/geoask.duckdb"))
    a = ap.parse_args()
    build(a.raw, a.out)
