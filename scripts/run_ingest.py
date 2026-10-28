"""Milestone M1: ingest a PDF and print a summary.

Usage:
  python scripts/run_ingest.py --pages 95-97        # uses the PDF in data/samples
  python scripts/run_ingest.py data/samples/book.pdf --pages 1-3
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.ingest import ingest  # noqa: E402

SAMPLES = ROOT / "data" / "samples"


def pick_pdf(arg):
    if arg:
        path = Path(arg)
        if not path.exists():
            names = [p.name for p in SAMPLES.glob("*.pdf")] or "none"
            sys.exit(f"File not found: {path}\nPDFs in data/samples: {names}")
        return path
    pdfs = sorted(SAMPLES.glob("*.pdf"))
    if not pdfs:
        sys.exit("No PDF found in data/samples. Download one (e.g. OpenStax "
                 "Introductory Statistics 2e) and save it into that folder first.")
    if len(pdfs) > 1:
        print(f"Several PDFs found; using {pdfs[0].name}. Pass a path to choose another.")
    return pdfs[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None, help="e.g. 95-97 or 2,5")
    args = p.parse_args()

    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    print(f"Ingested {pdf.name} -> output/{doc.id}/ ({len(doc.pages)} page(s))")
    for page in doc.pages:
        kind = "SCANNED" if page.is_scanned else "digital"
        print(f"  page {page.number}: {kind}, {len(page.text_blocks)} text blocks, "
              f"{len(page.embedded_images)} embedded image(s)")
        for img in page.embedded_images:
            b = img.bbox
            print(f"     image at ({b.x0:.0f},{b.y0:.0f})-({b.x1:.0f},{b.y1:.0f}) -> {img.path}")


if __name__ == "__main__":
    main()
