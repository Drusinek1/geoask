"""Download Census Bureau population estimates for every incorporated place.

Writes data/raw/census_place_population.csv with columns geoid,name,state_code,population,year.
Source: Population Estimates Program, Vintage 2023 "sub-est2023.csv" (no API key needed).
SUMLEV 162 = incorporated place; GEOID = STATE (2) + PLACE (5), matching Finding Flock's geoid.

  python etl/fetch_census.py
"""
from __future__ import annotations

import csv
import io
import urllib.request
from pathlib import Path

try:  # corporate/AV TLS interception on some Windows machines
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "census_place_population.csv"

FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
    "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
    "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
    "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}


def main() -> None:
    with urllib.request.urlopen(URL, timeout=300) as r:
        text = r.read().decode("latin-1")
    rows = csv.DictReader(io.StringIO(text))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    n = 0
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["geoid", "name", "state_code", "population", "year"])
        for r in rows:
            if r["SUMLEV"] != "162":
                continue
            geoid = r["STATE"].zfill(2) + r["PLACE"].zfill(5)
            if geoid in seen:  # places spanning counties repeat; the first row is the whole place
                continue
            seen.add(geoid)
            w.writerow([geoid, r["NAME"], FIPS_TO_STATE.get(r["STATE"].zfill(2), ""),
                        int(r["POPESTIMATE2023"]), 2023])
            n += 1
    print(f"wrote {n} places to {OUT}")


if __name__ == "__main__":
    main()
