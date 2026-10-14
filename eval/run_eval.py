"""Run the GeoAsk eval set and report accuracy.

Each case in cases.json has a question plus one or more checks:
  gold_sql           - a query whose single value must appear in the answer text
                       (numbers are matched ignoring thousands separators; `tolerance`
                       allows a numeric slack, useful for percentages)
  gold_text          - at least one of these strings must appear in the answer
  gold_text_sql      - like gold_text but the string comes from a query
  expect_layer       - whether a map layer should have been produced
  expect_layer_rows  - exact feature count on the first layer
  expect_refusal_phrase - for unanswerable questions: answer must contain one of these

Gold values are computed from the database at run time, so the eval stays
valid when the data is rebuilt.

  python eval/run_eval.py                 # all cases
  python eval/run_eval.py --only map_     # id prefix filter
  python eval/run_eval.py --out eval/results.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geoask.agent import GeoAskAgent  # noqa: E402
from geoask.db import GeoDB  # noqa: E402

NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUM_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def check_case(case: dict, answer, db: GeoDB) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    text = answer.text
    ok = True

    if "gold_sql" in case:
        gold = db.run_sql(case["gold_sql"]).rows[0][0]
        tol = float(case.get("tolerance", 0))
        found = any(abs(n - float(gold)) <= tol for n in numbers_in(text))
        if not found:
            ok = False
            reasons.append(f"expected number {gold} not in answer")

    texts = list(case.get("gold_text", []))
    if "gold_text_sql" in case:
        texts.append(str(db.run_sql(case["gold_text_sql"]).rows[0][0]))
    if texts and not any(t.lower() in text.lower() for t in texts):
        ok = False
        reasons.append(f"expected one of {texts} in answer")

    if case.get("expect_refusal_phrase"):
        if not any(p.lower() in text.lower() for p in case["expect_refusal_phrase"]):
            ok = False
            reasons.append("expected the agent to say the data cannot answer this")
        if any(s.tool == "show_on_map" for s in answer.steps):
            ok = False
            reasons.append("should not map an unanswerable question")

    has_layer = len(answer.layers) > 0
    if case.get("expect_layer") and not has_layer:
        ok = False
        reasons.append("expected a map layer")
    if not case.get("expect_layer") and has_layer and case.get("expect_layer") is False and "map" not in case["question"].lower():
        # Mapping when not asked is not wrong, just noted.
        reasons.append("note: produced a map layer without being asked")
    if "expect_layer_rows" in case and has_layer:
        n = len(answer.layers[0].geojson["features"])
        if n != case["expect_layer_rows"]:
            ok = False
            reasons.append(f"layer has {n} features, expected {case['expect_layer_rows']}")

    if any(not s.ok for s in answer.steps):
        reasons.append(f"note: {sum(not s.ok for s in answer.steps)} failed SQL attempt(s) before recovering")
    return ok, reasons


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=Path(__file__).parent / "cases.json", type=Path)
    ap.add_argument("--only", default="", help="run only case ids starting with this")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results.json")
    ap.add_argument("--effort", default="medium")
    a = ap.parse_args()

    cases = [c for c in json.loads(a.cases.read_text()) if c["id"].startswith(a.only)]
    db = GeoDB()
    agent = GeoAskAgent(db, effort=a.effort)

    results = []
    passed = 0
    t_all = time.perf_counter()
    for c in cases:
        t0 = time.perf_counter()
        try:
            ans = agent.ask(c["question"])
            ok, reasons = check_case(c, ans, db)
            rec = {
                "id": c["id"], "question": c["question"], "pass": ok, "reasons": reasons,
                "answer": ans.text, "steps": [s.tool for s in ans.steps],
                "sql": [s.input.get("sql") for s in ans.steps if "sql" in s.input],
                "layers": len(ans.layers), "input_tokens": ans.input_tokens,
                "output_tokens": ans.output_tokens, "elapsed_ms": ans.elapsed_ms,
            }
        except Exception as e:  # keep the run going; record the failure
            ok, rec = False, {"id": c["id"], "question": c["question"], "pass": False,
                              "reasons": [f"exception: {e}"], "elapsed_ms": int((time.perf_counter() - t0) * 1000)}
        passed += ok
        results.append(rec)
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {c['id']:<24} {rec['elapsed_ms'] / 1000:5.1f}s  {'; '.join(rec['reasons'])}")

    total = len(cases)
    summary = {
        "passed": passed, "total": total, "accuracy": round(passed / total, 3) if total else None,
        "model": agent.model, "effort": a.effort,
        "total_input_tokens": sum(r.get("input_tokens", 0) for r in results),
        "total_output_tokens": sum(r.get("output_tokens", 0) for r in results),
        "wall_s": round(time.perf_counter() - t_all, 1),
    }
    a.out.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"\n{passed}/{total} passed ({summary['accuracy']:.0%})  "
          f"tokens in={summary['total_input_tokens']:,} out={summary['total_output_tokens']:,}  "
          f"{summary['wall_s']}s  -> {a.out}")


if __name__ == "__main__":
    main()
