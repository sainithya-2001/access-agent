"""Milestone M1: ingest a PDF and print a summary.

Usage:
  python scripts/run_ingest.py data/samples/chapter.pdf --pages 1-3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest import ingest  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("--pages", default=None, help="e.g. 1-3 or 2,5")
    args = p.parse_args()

    doc = ingest(args.pdf, args.pages)
    print(f"Document {doc.id}: {len(doc.pages)} page(s) → output/{doc.id}/")
    for page in doc.pages:
        kind = "SCANNED" if page.is_scanned else "digital"
        print(f"  page {page.number}: {kind}, {len(page.text_blocks)} text blocks, "
              f"{len(page.embedded_images)} embedded image(s)")
        for img in page.embedded_images:
            b = img.bbox
            print(f"     image at ({b.x0:.0f},{b.y0:.0f})–({b.x1:.0f},{b.y1:.0f}) → {img.path}")


if __name__ == "__main__":
    main()
