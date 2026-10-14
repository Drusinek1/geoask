"""FastAPI server for GeoAsk.

  uvicorn app:app --reload
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from geoask.agent import GeoAskAgent
from geoask.db import GeoDB

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = FastAPI(title="GeoAsk", version="0.1.0")

_db = GeoDB(os.environ.get("GEOASK_DB", ROOT / "data" / "geoask.duckdb"))
_agent = GeoAskAgent(_db, effort=os.environ.get("GEOASK_EFFORT", "medium"))

EXAMPLES = [
    "Which US states have the most data centers with coordinates? Show the top 5 on a map.",
    "How many AWS data centers are within 50 km of a Microsoft data center? Map them.",
    "Map the 10 US cities with the most ALPR cameras.",
    "Which cities have both a lot of data centers and a lot of license plate readers?",
    "Where are Google's data centers in Europe?",
    "What share of ALPR cameras are made by Flock, and which states lean most on Flock?",
]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/examples")
def examples() -> list[str]:
    return EXAMPLES


@app.get("/api/schema")
def schema() -> dict:
    return {"schema": _db.schema_text()}


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    try:
        return _agent.ask(req.question).to_dict()
    except Exception as e:  # surface API/auth errors to the UI instead of a bare 500
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e


app.mount("/static", StaticFiles(directory=STATIC), name="static")
