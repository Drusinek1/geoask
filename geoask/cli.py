"""Ask GeoAsk a question from the command line.

  python -m geoask.cli "Which US state has the most data centers with coordinates?"
"""
from __future__ import annotations

import argparse
import json

from .agent import GeoAskAgent
from .db import GeoDB


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--json", action="store_true", help="print the full Answer as JSON")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    a = ap.parse_args()

    agent = GeoAskAgent(GeoDB(), effort=a.effort)
    ans = agent.ask(a.question)

    if a.json:
        print(json.dumps(ans.to_dict(), indent=2, default=str))
        return

    for i, s in enumerate(ans.steps, 1):
        mark = "ok " if s.ok else "ERR"
        line = f"[{i}] {mark} {s.tool}"
        if s.row_count is not None:
            line += f"  rows={s.row_count}{'+' if s.truncated else ''}  {s.elapsed_ms}ms"
        print(line)
        if s.tool in ("run_sql", "show_on_map"):
            print("     " + " ".join(s.input["sql"].split())[:300])
        if s.error:
            print("     " + s.error)
    print()
    print(ans.text)
    print(f"\n-- {ans.model}  in={ans.input_tokens} out={ans.output_tokens}  {ans.elapsed_ms}ms  layers={len(ans.layers)}")


if __name__ == "__main__":
    main()
