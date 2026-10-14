"""Raster sampling for the agent: land cover around a point.

NOAA C-CAP 2021 land cover (30 m, EPSG:5070). The tile in data/raw/ccap covers
roughly Washington DC / Northern Virginia / central Maryland. Everything here is
plain numpy over a rasterio window; no model involvement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform, transform_bounds

CCAP_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "ccap"

CCAP_CLASSES = {
    1: "Background", 2: "High Intensity Developed", 3: "Medium Intensity Developed",
    4: "Low Intensity Developed", 5: "Developed Open Space", 6: "Cultivated", 7: "Pasture/Hay",
    8: "Grassland", 9: "Deciduous Forest", 10: "Evergreen Forest", 11: "Mixed Forest",
    12: "Scrub/Shrub", 13: "Palustrine Forested Wetland", 14: "Palustrine Scrub/Shrub Wetland",
    15: "Palustrine Emergent Wetland", 16: "Estuarine Forested Wetland",
    17: "Estuarine Scrub/Shrub Wetland", 18: "Estuarine Emergent Wetland",
    19: "Unconsolidated Shore", 20: "Bare Land", 21: "Water", 22: "Palustrine Aquatic Bed",
    23: "Estuarine Aquatic Bed", 25: "Snow/Ice",
}
# Coarser groups that are easier to reason about in an answer.
CCAP_GROUPS = {
    **{k: "Developed" for k in (2, 3, 4, 5)},
    **{k: "Agriculture" for k in (6, 7)},
    8: "Grassland", **{k: "Forest" for k in (9, 10, 11)}, 12: "Scrub/Shrub",
    **{k: "Wetland" for k in (13, 14, 15, 16, 17, 18, 22, 23)},
    19: "Shore/Bare", 20: "Shore/Bare", 21: "Water", 25: "Snow/Ice",
}


@dataclass
class Coverage:
    west: float
    south: float
    east: float
    north: float

    def contains(self, lat: float, lon: float) -> bool:
        """Approximate (lat/lon bounding box); LandCover.summary does the exact check."""
        return self.south <= lat <= self.north and self.west <= lon <= self.east


class LandCover:
    def __init__(self, path: Path | None = None):
        if path is None:
            tifs = sorted(CCAP_DIR.glob("*.tif"))
            if not tifs:
                raise FileNotFoundError(f"no C-CAP .tif in {CCAP_DIR}")
            path = tifs[0]
        self.path = path
        self._ds = rasterio.open(path)
        w, s, e, n = transform_bounds(self._ds.crs, "EPSG:4326", *self._ds.bounds)
        self.coverage = Coverage(round(w, 4), round(s, 4), round(e, 4), round(n, 4))
        self.resolution_m = float(self._ds.res[0])
        self.year = 2021

    def summary(self, lat: float, lon: float, radius_m: float = 500) -> dict:
        """Class share within a circle of radius_m around (lat, lon)."""
        radius_m = float(max(30, min(radius_m, 5000)))
        xs, ys = transform("EPSG:4326", self._ds.crs, [lon], [lat])
        x, y = xs[0], ys[0]
        b = self._ds.bounds
        # The tile is rotated relative to lat/lon, so test containment in the raster's own CRS.
        if not (b.left <= x <= b.right and b.bottom <= y <= b.top):
            raise ValueError(
                f"point ({lat}, {lon}) is outside land-cover coverage (approx. "
                f"W {self.coverage.west}, S {self.coverage.south}, E {self.coverage.east}, N {self.coverage.north})"
            )
        half = radius_m
        row0, col0 = self._ds.index(x - half, y + half)
        row1, col1 = self._ds.index(x + half, y - half)
        row0, col0 = max(row0, 0), max(col0, 0)
        row1, col1 = min(row1 + 1, self._ds.height), min(col1 + 1, self._ds.width)
        if row1 <= row0 or col1 <= col0:
            raise ValueError(f"point ({lat}, {lon}) is at the edge of land-cover coverage")
        window = rasterio.windows.Window(col0, row0, col1 - col0, row1 - row0)
        arr = self._ds.read(1, window=window)

        # Circular mask in pixel space.
        res = self.resolution_m
        rr, cc = np.mgrid[row0:row1, col0:col1]
        t = self._ds.transform  # north-up grid: pixel centers from the affine directly
        px_x = t.c + (cc + 0.5) * t.a
        px_y = t.f + (rr + 0.5) * t.e
        dist = np.hypot(px_x - x, px_y - y)
        inside = (dist <= radius_m) & (arr != self._ds.nodata) & (arr != 1)
        vals, counts = np.unique(arr[inside], return_counts=True)
        total = int(counts.sum())
        if total == 0:
            raise ValueError("no land-cover pixels inside the radius (all nodata)")

        classes = sorted(
            ({"class": CCAP_CLASSES.get(int(v), f"class {int(v)}"), "pct": round(100 * int(c) / total, 1)}
             for v, c in zip(vals, counts)),
            key=lambda d: -d["pct"],
        )
        groups: dict[str, int] = {}
        for v, c in zip(vals, counts):
            g = CCAP_GROUPS.get(int(v), "Other")
            groups[g] = groups.get(g, 0) + int(c)
        return {
            "lat": lat, "lon": lon, "radius_m": radius_m, "year": self.year,
            "pixels": total, "area_km2": round(total * res * res / 1e6, 3),
            "groups": {k: round(100 * v / total, 1) for k, v in sorted(groups.items(), key=lambda kv: -kv[1])},
            "classes": classes,
        }

    def developed_pct(self, lat: float, lon: float, radius_m: float = 500) -> float | None:
        try:
            return self.summary(lat, lon, radius_m)["groups"].get("Developed", 0.0)
        except ValueError:
            return None
