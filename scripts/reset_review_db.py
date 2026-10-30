"""Rebuild the review database from one results file, with no staff edits.

Usage:
  python scripts/reset_review_db.py output/<doc>/agent_results_meta.json --pages 84-97,109,124,130
  python scripts/reset_review_db.py RESULTS --pages ... --db output/scratch.db   # try it elsewhere first

RESULTS is a JSON list of FigureResult (what scripts/run_agent.py writes). The page layout (text
blocks and regions, used by the page view and the export) comes from ingest + detect, which are
local and cached: no model calls unless --page-check finds no cached detection. Each figure's region
comes from the results file, so its crop is the one the agent described.

The new database is built in a temp file and swapped in only when complete; the old one is kept as
<db>.bak-<time>. Stop the review server first: an open database (a -wal file next to it) is refused.
Results that carry staff decisions (approved, edited, rejected) are refused: the point of a reset is
that no staff edits remain.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import _doc_id, ingest  # noqa: E402
from app.models import FigureResult, Status  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402

STAFF = (Status.approved, Status.edited, Status.rejected)


def load_results(path: Path) -> list[FigureResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.exit(f"{path} is not a list of figure results (benchmark files in eval/results/ are scores, "
                 "not results; use output/<doc>/agent_results*.json).")
    results = [FigureResult.model_validate(x) for x in data]
    staff = [r.region.id for r in results if r.status in STAFF]
    if staff:
        sys.exit(f"{path} has staff decisions ({', '.join(staff)}); a reset starts with none.")
    ids = [r.region.id for r in results]
    if len(set(ids)) != len(ids):
        sys.exit(f"{path} has the same region twice.")
    return results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results", type=Path, help="JSON list of FigureResult, e.g. output/<doc>/agent_results_meta.json")
    p.add_argument("--pdf", default=None, help="the PDF the results came from (default: the one in data/samples)")
    p.add_argument("--pages", required=True, help="pages of the document to rebuild, e.g. 84-97,109,124,130")
    p.add_argument("--page-check", action="store_true", help="layout from the page check, as the results were run")
    p.add_argument("--db", type=Path, default=store.DB_PATH, help=f"database to replace (default {store.DB_PATH})")
    args = p.parse_args()

    results = load_results(args.results)
    db = args.db
    if Path(f"{db}-wal").exists():
        sys.exit(f"{db} is open (found {db}-wal). Stop the review server, then run this again.")

    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    pages = {pg.number for pg in doc.pages}
    outside = sorted({r.region.page for r in results} - pages)
    if outside:
        sys.exit(f"Results include pages {outside}, outside --pages {args.pages}.")
    regions, _ = detect(doc, use_sam=args.page_check, use_check=args.page_check)

    # The layout's figure regions are the results' regions (same id → replaced; new → added).
    by_id = {r.region.id: r.region for r in results}
    layout = [by_id.pop(r.id, r) for r in regions]
    layout += sorted(by_id.values(), key=lambda r: (r.page, r.bbox.y0))
    undescribed = [r.id for r in layout if r.kind == "figure" and r.id not in {x.region.id for x in results}]
    missing_crops = [r.region.id for r in results if r.region.crop_path and not Path(r.region.crop_path).exists()]

    tmp = db.with_name(db.name + ".new")
    for f in (tmp, Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
        f.unlink(missing_ok=True)
    con = store.connect(tmp)
    key = store.document_key(_doc_id(pdf), args.pages)
    store.create_document(con, key, _doc_id(pdf), str(pdf), pdf.name, args.pages)
    store.save_layout(con, key, doc, layout)
    for res in results:
        store.save_figure(con, key, res)
    store.set_progress(con, key, "ready", f"{len(results)} figure{'' if len(results) == 1 else 's'}")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    for f in (Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
        f.unlink(missing_ok=True)

    if db.exists():
        backup = db.with_name(f"{db.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        db.replace(backup)
        print(f"Old database kept as {backup}")
    os.replace(tmp, db)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    print(f"{db}: document {key}, {len(results)} figures from {args.results} "
          f"({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}), 0 staff edits")
    if undescribed:
        print(f"Warning: detected figures not in the results file (shown as not yet described): {', '.join(undescribed)}")
    if missing_crops:
        print(f"Warning: crop images missing for {', '.join(missing_crops)}")


if __name__ == "__main__":
    main()
