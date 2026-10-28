"""Milestone M2: detect figures and tables, and draw overlays.

Usage:
  python scripts/run_detect.py --pages 95-97                 # all layers (uses Meta API)
  python scripts/run_detect.py --pages 95-97 --free-only     # no API calls at all
  python scripts/run_detect.py --pages 95-97 --no-sam        # skip SAM, keep the Muse Spark check

Overlays: output/<doc id>/overlays/page_NNN.png
  blue = embedded image, green = vector chart, orange = table,
  purple = SAM 3.1, red = added by Muse Spark check, magenta = Spark check refined by SAM
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--free-only", action="store_true", help="no model calls")
    p.add_argument("--no-sam", action="store_true")
    p.add_argument("--no-check", action="store_true", help="skip the Muse Spark page check")
    args = p.parse_args()

    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    regions, notes = detect(doc,
                            use_sam=not (args.free_only or args.no_sam),
                            use_check=not (args.free_only or args.no_check))

    print(f"{pdf.name}: {len(regions)} region(s) on {len(doc.pages)} page(s)")
    for r in regions:
        b = r.bbox
        extra = f" | {r.caption}" if r.caption else ""
        rows = f" | {len(r.table_rows)} rows" if r.table_rows else ""
        print(f"  {r.id:10} {r.kind:6} {r.source:16} ({b.x0:.0f},{b.y0:.0f})-({b.x1:.0f},{b.y1:.0f}){extra}{rows}")
    if notes:
        print("Notes:")
        for n in notes:
            print("  -", n)
    print(f"Overlays: output/{doc.id}/overlays/")


if __name__ == "__main__":
    main()
