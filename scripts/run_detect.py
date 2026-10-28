"""Milestone M2: detect figures and tables, and draw overlays.

Usage:
  python scripts/run_detect.py --pages 95-97                 # all layers (uses Meta API)
  python scripts/run_detect.py --pages 95-97 --free-only     # no API calls at all
  python scripts/run_detect.py --pages 95-97 --no-sam        # skip SAM, keep the Muse Spark check
  python scripts/run_detect.py --pages 85,89 --test-check    # page check alone vs true image boxes

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

from app.detect import _iou, _rect, detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--free-only", action="store_true", help="no model calls")
    p.add_argument("--no-sam", action="store_true")
    p.add_argument("--no-check", action="store_true", help="skip the Muse Spark page check")
    p.add_argument("--test-check", action="store_true",
                   help="turn off the free figure layers, run the page check + SAM, and score its boxes "
                        "against the true embedded-image boxes (IoU)")
    p.add_argument("--sam-extend", choices=["auto", "labels", "union", "pad", "none"], default="auto",
                   help="how to widen SAM's tight boxes (auto: labels on digital pages, "
                        "SAM_EXTEND_SCANNED on scanned pages)")
    args = p.parse_args()

    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    regions, notes = detect(doc,
                            use_sam=not (args.free_only or args.no_sam),
                            use_check=args.test_check or not (args.free_only or args.no_check),
                            free_figures=not args.test_check, sam_extend=args.sam_extend)

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
    if args.test_check:
        score_check(doc, regions)


def score_check(doc, regions, match_iou: float = 0.5) -> None:
    """Each true figure (embedded image) is found if a check box overlaps it with IoU >= match_iou."""
    print(f"\nPage check vs true figure boxes (found = IoU >= {match_iou}):")
    ious, extra, by_source = [], 0, {}
    for page in doc.pages:
        truth = [_rect(i.bbox) for i in page.embedded_images]
        figs = [r for r in regions if r.page == page.number and r.kind == "figure"]
        boxes = [_rect(r.bbox) for r in figs]
        for t in truth:
            best, src = max(((_iou(t, _rect(r.bbox)), r.source) for r in figs), default=(0.0, "none"))
            ious.append(best)
            by_source.setdefault(src, []).append(best)
            print(f"  p{page.number}: true ({t.x0:.0f},{t.y0:.0f})-({t.x1:.0f},{t.y1:.0f}) "
                  f"{'FOUND' if best >= match_iou else 'MISSED'}  IoU {best:.2f}  [{src}]")
        wrong = [b for b in boxes if all(_iou(t, b) < match_iou for t in truth)]
        extra += len(wrong)
        for b in wrong:
            print(f"  p{page.number}: extra box ({b.x0:.0f},{b.y0:.0f})-({b.x1:.0f},{b.y1:.0f}) matches no figure")
    found = [x for x in ious if x >= match_iou]
    print(f"Found {len(found)}/{len(ious)}, missed {len(ious) - len(found)}, extra boxes {extra}; "
          f"average IoU {sum(ious) / max(len(ious), 1):.2f} over all figures"
          + (f", {sum(found) / len(found):.2f} over found" if found else ""))
    for src, v in sorted(by_source.items()):
        print(f"  average IoU where the box came from {src}: {sum(v) / len(v):.2f} ({len(v)} figures)")


if __name__ == "__main__":
    main()
