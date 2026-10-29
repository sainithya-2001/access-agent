"""Mean and range of each benchmark metric over repeated runs (run_benchmark.py --rep N).

  python scripts/summarize_repeats.py --set dev --reps 3              # reps 1-3
  python scripts/summarize_repeats.py --set dev --reps 3 --first 4    # reps 4-6
Writes eval/results/<set>_repeats.md (or <set>_repeats_r4-6.md) and prints it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

METRICS = [
    ("Unsafe approvals (target 0)", lambda s: s["unsafe_approvals"]),
    ("  caught by code checks", lambda s: s["unsafe_approvals_code_scored"]),
    ("  caught by judge, code-scored items", lambda s: s.get("unsafe_approvals_judge_on_code_scored", 0)),
    ("  judged-only items", lambda s: s["unsafe_approvals_judged_by_model"]),
    ("Correct outcome (of 19)", lambda s: s["correct_outcome"]),
    ("Values correct (of 162)", lambda s: s["values_correct"]),
    ("Values wrong", lambda s: s["values_wrong"]),
    ("Values not in the data table", lambda s: s["values_missing"]),
    ("Items with invented content", lambda s: s["items_with_invented_content"]),
    ("Items with conflicting rows", lambda s: s.get("items_with_conflicting_rows", 0)),
    ("Judged-only items accurate (of 5)", lambda s: s["judged_by_model"]["accurate"]),
    ("Auto-approved (of 19)", lambda s: s["auto_approved"]),
    ("Sent to review (of 19)", lambda s: s["sent_to_review"]),
    ("Fresh-run cost per run ($)", lambda s: s["cost_usd_fresh_run"]),
]


def fmt(xs: list[float]) -> str:
    m = sum(xs) / len(xs)
    lo, hi = min(xs), max(xs)
    f = (lambda v: f"{v:.2f}") if any(x != int(x) for x in xs) else (lambda v: f"{v:g}")
    return f"{m:.2f} ({f(lo)}–{f(hi)})" if lo != hi else f"{f(m)} (all runs)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", default="dev", dest="which")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--first", type=int, default=1, help="number of the first repeat")
    args = p.parse_args()
    reps = range(args.first, args.first + args.reps)
    runs = {}
    for b in ("meta", "baseten"):
        runs[b] = [json.loads((ROOT / "eval" / "results" / f"{args.which}_{b}_r{r}.json").read_text(encoding="utf-8"))
                   for r in reps]
    out = [f"# {args.which} set, runs {reps[0]}–{reps[-1]} per backend: mean (min–max)", "",
           "| Metric | Muse Spark (Meta) | Muse Spark Open (Baseten) |", "|---|---|---|"]
    for name, f in METRICS:
        out.append(f"| {name} | " + " | ".join(fmt([f(r["summary"]) for r in runs[b]]) for b in runs) + " |")

    out += ["", "## Unsafe approvals by item (runs out of " + str(args.reps) + ")", "",
            "| Item | Meta | Baseten |", "|---|---|---|"]
    counts = {b: Counter(i["id"] for r in runs[b] for i in r["items"] if i.get("unsafe")) for b in runs}
    for item in sorted(set(counts["meta"]) | set(counts["baseten"])):
        out.append(f"| {item} | {counts['meta'][item]} | {counts['baseten'][item]} |")

    out += ["", "## Spend and failures", ""]
    for b in runs:
        spent = sum(r["summary"]["cost_usd"] for r in runs[b])
        unc = sum(r["summary"]["uncached_calls"] for r in runs[b])
        errors = [f"{i['id']} (run {k})" for k, r in zip(reps, runs[b]) for i in r["items"]
                  if any(t["action"] == "error" for t in i.get("trace") or [])]
        out.append(f"- {b}: {unc} uncached calls, ${spent:.2f} spent in these runs (agent + judge); "
                   f"processing errors: {', '.join(errors) or 'none'}")
    text = "\n".join(out) + "\n"
    name = f"{args.which}_repeats.md" if args.first == 1 else f"{args.which}_repeats_r{reps[0]}-{reps[-1]}.md"
    (ROOT / "eval" / "results" / name).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
