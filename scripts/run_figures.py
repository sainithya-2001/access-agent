"""M3: classify → extract values → describe → verify, for every detected figure.

This is the straight pipeline (no retries yet). M5 wraps these tools in the agent loop.

Usage:
  python scripts/run_figures.py --pages 95-97
  python scripts/run_figures.py --pages 95-97 --backend baseten
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import tools  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from app.models import FigureResult, Status  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--backend", choices=["meta", "baseten"], default=None)
    args = p.parse_args()

    doc = ingest(pick_pdf(args.pdf), args.pages)
    regions, _ = detect(doc)
    figures = [r for r in regions if r.kind == "figure"]
    print(f"{len(figures)} figure(s) to process (tables are handled as text)\n")

    results = []
    for r in figures:
        res = FigureResult(region=r)
        res.classification = tools.classify_region(r, args.backend)
        kind = res.classification.type.value
        print(f"== {r.id} | {r.caption or 'no caption'} | {kind}")
        if res.classification.is_decorative:
            res.status = Status.auto_approved
            print("   decorative → alt=\"\"\n")
            results.append(res)
            continue

        res.extracted = tools.extract_values(r, kind, args.backend)
        res.description = tools.describe(r, kind, args.backend)
        res.verification = tools.verify(r, res.description, res.extracted, args.backend)
        v = res.verification
        res.status = Status.auto_approved if v.passed else Status.needs_review

        print(f"   ALT: {res.description.alt_text}")
        print(f"   LONG: {res.description.long_description[:400]}{'...' if len(res.description.long_description) > 400 else ''}")
        if res.extracted.data:
            vals = ", ".join(f"{d.label}={d.value:g}" for d in res.extracted.data if d.value is not None)
            print(f"   EXTRACTED ({'estimated' if res.extracted.values_estimated else 'printed'}): {vals}")
        print(f"   VERIFY: {'PASS' if v.passed else 'FAIL'} (confidence {v.confidence:.2f}) → {res.status.value}")
        for i in v.issues:
            print(f"     - [{i.source}] {i.kind}: {i.detail}")
        print()
        results.append(res)

    out = Path("output") / doc.id / "figures.json"
    out.write_text(json.dumps([x.model_dump(mode="json") for x in results], indent=2))
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
