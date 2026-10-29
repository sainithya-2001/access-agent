"""M5: run the agent on every detected figure and print each figure's trace.

Usage:
  python scripts/run_agent.py --pages 96
  python scripts/run_agent.py --pages 96 --inject wrong_number   # start from a description with a
                                                                 # planted error; watch the agent fix it
  python scripts/run_agent.py --pages 96 --backend baseten
Inject types: wrong_number, wrong_trend, missing_label, invented_detail, wrong_label
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.agent import tools  # noqa: E402
from app.agent.orchestrator import FigureAgent  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from app.llm import LOG_PATH, get_client  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402
from scripts.test_verifier import ERROR_TYPES, plant  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--backend", choices=["meta", "baseten"], default=None)
    p.add_argument("--inject", choices=list(ERROR_TYPES), default=None)
    args = p.parse_args()

    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    regions, _ = detect(doc)
    figures = [r for r in regions if r.kind == "figure"]
    backend = get_client(args.backend)[2]
    log_start = sum(1 for _ in open(LOG_PATH, encoding="utf-8")) if LOG_PATH.exists() else 0

    results, seconds = [], {}
    for r in figures:
        t0 = time.time()
        start = None
        if args.inject:
            kind = tools.classify_region(r, args.backend).type.value
            good = tools.describe(r, kind, args.backend)
            start, changed = plant(good, args.inject, args.backend)
            print(f"[test] planted {args.inject}: {changed}")

        res = FigureAgent(r, str(pdf), args.backend, start_description=start).run()
        print(f"\n== {r.id} | {r.caption or 'no caption'} → {res.status.value}")
        for t in res.trace:
            print(f"  {t.step:2}. [{t.action}] {t.summary}")
        if res.description and res.description.alt_text:
            print(f"  ALT: {res.description.alt_text}")
        results.append(res)
        seconds[r.id] = round(time.time() - t0, 1)

    calls = [json.loads(x) for x in open(LOG_PATH, encoding="utf-8")][log_start:] if LOG_PATH.exists() else []
    new = [c for c in calls if not c.get("cached")]
    print(f"\nBackend {backend}: {len(calls)} model calls ({len(new)} uncached, "
          f"{sum(c.get('seconds') or 0 for c in new):.0f}s waiting on the model); "
          f"agent wall time {sum(seconds.values()):.0f}s")
    suffix = f"_{backend}" + (f"_{args.inject}" if args.inject else "")
    (Path("output") / doc.id / f"agent_timing{suffix}.json").write_text(json.dumps(
        {"seconds_per_figure": seconds, "calls": calls}, indent=2), encoding="utf-8")
    out = Path("output") / doc.id / f"agent_results{suffix}.json"
    out.write_text(json.dumps([x.model_dump(mode="json") for x in results], indent=2), encoding="utf-8")
    auto = sum(1 for x in results if x.status.value == "auto_approved")
    print(f"\n{auto}/{len(results)} auto-approved, {len(results) - auto} sent to human review. Saved {out}")


if __name__ == "__main__":
    main()
