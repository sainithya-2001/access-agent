"""Compare two agent runs on the same figures: Meta (Muse Spark) vs Baseten (Muse Spark Open).

  python scripts/run_agent.py --pages 84-97 --backend meta
  python scripts/run_agent.py --pages 84-97 --backend baseten
  python scripts/compare_backends.py            # reads output/<doc>/agent_results_<backend>.json

Prints statuses, verifier results, actions taken, alt texts, and model latency per step.
Latency uses uncached calls only. A step answered from cache in a run has no timing in that run,
so per-step latency also falls back to every earlier uncached call of that backend in the log.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.llm import LOG_PATH  # noqa: E402

A, B = "meta", "baseten"


def load(backend: str) -> tuple[dict, dict]:
    res = sorted(ROOT.glob(f"output/*/agent_results_{backend}.json"), key=lambda p: p.stat().st_mtime)
    if not res:
        sys.exit(f"No agent_results_{backend}.json; run scripts/run_agent.py --backend {backend} first.")
    results = {r["region"]["id"]: r for r in json.loads(res[-1].read_text(encoding="utf-8"))}
    timing = res[-1].with_name(f"agent_timing_{backend}.json")
    return results, (json.loads(timing.read_text(encoding="utf-8")) if timing.exists() else {})


def summary(r: dict) -> dict:
    v = r.get("verification") or {}
    trace = r.get("trace", [])
    return {
        "status": r["status"],
        "conf": v.get("confidence"),
        "issues": sorted({i["kind"] for i in v.get("issues", [])}),
        "verifies": [t["summary"].split(":")[0] for t in trace if t["action"] == "verify"],
        "actions": [t["action"] for t in trace if t["action"] in
                    ("reread_value", "rewrite", "recrop", "mark_decorative", "escalate", "error")],
        "type": (r.get("classification") or {}).get("type"),
        "series": (r.get("extracted") or {}).get("series_identified_by"),
        "alt": (r.get("description") or {}).get("alt_text") or "",
        "reason": r.get("escalation_reason"),
    }


def step_name(step: str) -> str:
    return step.split(":")[0].replace("_repair", "")


def latency(backend: str, run_calls: list[dict]) -> dict[str, list[float]]:
    by_step: dict[str, list[float]] = defaultdict(list)
    for c in run_calls:
        if not c.get("cached") and c.get("seconds") is not None:
            by_step[step_name(c["step"])].append(c["seconds"])
    if LOG_PATH.exists():  # earlier uncached calls, for steps this run answered from cache
        hist: dict[str, list[float]] = defaultdict(list)
        for line in open(LOG_PATH, encoding="utf-8"):
            c = json.loads(line)
            if c.get("backend") == backend and not c.get("cached") and c.get("seconds") is not None \
                    and not str(c.get("model", "")).startswith("sam"):
                hist[step_name(c["step"])].append(c["seconds"])
        for k, v in hist.items():
            by_step.setdefault(k, v)
    return by_step


def main() -> None:
    ra, ta = load(A)
    rb, tb = load(B)
    ids = [i for i in ra if i in rb]
    print(f"{len(ids)} figures in both runs ({A}: {len(ra)}, {B}: {len(rb)})\n")
    agree = 0
    for fid in ids:
        a, b = summary(ra[fid]), summary(rb[fid])
        same = a["status"] == b["status"]
        agree += same
        cap = ra[fid]["region"].get("caption") or "no caption"
        print(f"== {fid} {cap}  {'same status' if same else 'DIFFERENT status'}")
        for name, s in ((A, a), (B, b)):
            conf = f"{s['conf']:.2f}" if s["conf"] is not None else "—"
            print(f"  {name:8} {s['status']:14} conf {conf}  type {s['type']}  series {s['series']}  "
                  f"verify {' → '.join(s['verifies']) or '—'}  actions {', '.join(s['actions']) or 'none'}")
            if s["issues"]:
                print(f"           last issues: {', '.join(s['issues'])}")
            if s["reason"]:
                print(f"           reason: {s['reason'][:150]}")
            print(f"           alt: {s['alt']}")
    print(f"\nSame status on {agree}/{len(ids)} figures.")
    for name, res in ((A, ra), (B, rb)):
        st = defaultdict(int)
        for fid in ids:
            st[res[fid]["status"]] += 1
        confs = [res[f]["verification"]["confidence"] for f in ids if res[f].get("verification")]
        print(f"  {name:8} " + ", ".join(f"{v} {k}" for k, v in sorted(st.items()))
              + (f"; mean verifier confidence {statistics.mean(confs):.2f}" if confs else ""))

    print("\nModel latency per call, seconds (median, n):")
    la, lb = latency(A, ta.get("calls", [])), latency(B, tb.get("calls", []))
    for step in sorted(set(la) | set(lb)):
        cell = lambda d: f"{statistics.median(d[step]):6.1f} ({len(d[step])})" if d.get(step) else "     —    "
        print(f"  {step:16} {A} {cell(la)}   {B} {cell(lb)}")
    for name, t in ((A, ta), (B, tb)):
        calls = t.get("calls", [])
        new = [c for c in calls if not c.get("cached")]
        secs = sum((t.get("seconds_per_figure") or {}).get(f, 0) for f in ids)
        print(f"  {name:8} this run: {len(calls)} calls, {len(new)} uncached, agent wall time {secs:.0f}s "
              f"for {len(ids)} figures")


if __name__ == "__main__":
    main()
