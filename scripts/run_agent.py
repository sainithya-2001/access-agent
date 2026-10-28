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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.agent import tools  # noqa: E402
from app.agent.orchestrator import FigureAgent  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
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

    results = []
    for r in figures:
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

    out = Path("output") / doc.id / "agent_results.json"
    out.write_text(json.dumps([x.model_dump(mode="json") for x in results], indent=2), encoding="utf-8")
    auto = sum(1 for x in results if x.status.value == "auto_approved")
    print(f"\n{auto}/{len(results)} auto-approved, {len(results) - auto} sent to human review. Saved {out}")


if __name__ == "__main__":
    main()
