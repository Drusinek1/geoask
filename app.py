"""FastAPI server for GeoAsk.

  uvicorn app:app --reload
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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


class Budget:
    """Simple abuse guard for the public demo: per-IP and global question caps.

    Not a security boundary, just enough to keep one visitor (or a script) from
    draining the API budget. Tune with GEOASK_PER_IP_PER_HOUR / GEOASK_PER_DAY.
    """

    def __init__(self, per_ip_per_hour: int, per_day: int):
        self.per_ip, self.per_day = per_ip_per_hour, per_day
        self._ip: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            q = self._ip.setdefault(ip, deque())
            while q and q[0] < now - 3600:
                q.popleft()
            while self._all and self._all[0] < now - 86400:
                self._all.popleft()
            if len(q) >= self.per_ip:
                raise HTTPException(429, "Rate limit: this demo allows "
                                    f"{self.per_ip} questions per hour per visitor. Try again later.")
            if len(self._all) >= self.per_day:
                raise HTTPException(429, "The demo's daily question budget is used up. "
                                    "Clone the repo and run it with your own API key.")
            q.append(now)
            self._all.append(now)


_budget = Budget(int(os.environ.get("GEOASK_PER_IP_PER_HOUR", "10")),
                 int(os.environ.get("GEOASK_PER_DAY", "150")))


def _client_ip(request: Request) -> str:
    # HF Spaces and most proxies set X-Forwarded-For; fall back to the socket peer.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


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
def ask(req: AskRequest, request: Request) -> dict:
    _budget.check(_client_ip(request))
    try:
        return _agent.ask(req.question).to_dict()
    except Exception as e:  # surface API/auth errors to the UI instead of a bare 500
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e


app.mount("/static", StaticFiles(directory=STATIC), name="static")
