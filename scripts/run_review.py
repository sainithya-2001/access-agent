"""M7: run the pipeline into the review store, then open the review screen.

Usage:
  python scripts/run_review.py --pages 84-97,109,124,130
  uvicorn app.api:app --port 8000         # then open http://localhost:8000

Same pipeline as an upload in the UI (ingest → detect → agent → SQLite), but from the command line
so test data can be loaded without a browser. Cached, so re-runs cost $0.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app import pipeline, store  # noqa: E402
from app.ingest import _doc_id  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--page-check", action="store_true", help="run the Muse Spark page check + SAM 3.1")
    p.add_argument("--backend", choices=["meta", "baseten"], default=None)
    args = p.parse_args()

    pdf = pick_pdf(args.pdf)
    con = store.connect()
    key = store.document_key(_doc_id(pdf), args.pages)
    store.create_document(con, key, _doc_id(pdf), str(pdf), pdf.name, args.pages)
    calls = pipeline.run(con, key, pdf, args.pages, args.page_check, args.backend, echo=True)

    figs = store.figures(con, key)
    counts: dict[str, int] = {}
    for _, res in figs:
        counts[res.status.value] = counts.get(res.status.value, 0) + 1
    print(f"\nDocument {key}: {len(figs)} figures " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"Model calls: {calls['calls']} ({calls['uncached']} uncached)")
    print("Review it: uvicorn app.api:app --port 8000  →  http://localhost:8000")


if __name__ == "__main__":
    main()
