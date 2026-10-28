"""M6: PDF pages → output/<doc id>/accessible.html.

Usage:
  python scripts/run_export.py --pages 84-91,95-97
  python scripts/run_export.py --pages 84-91 --page-check     # also run the Muse Spark page check + SAM
  python scripts/run_export.py --pages 84-91 --no-agent       # layout only; every figure flagged for review

Runs ingest → detection → the agent on every figure (cached, so re-runs cost $0) → assemble.
Then check it: python scripts/check_a11y.py
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.agent.orchestrator import FigureAgent  # noqa: E402
from app.assemble import assemble  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from app.llm import LOG_PATH  # noqa: E402
from scripts.run_ingest import pick_pdf  # noqa: E402


def _log_lines() -> int:
    return sum(1 for _ in open(LOG_PATH, encoding="utf-8")) if Path(LOG_PATH).exists() else 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--pages", default=None)
    p.add_argument("--page-check", action="store_true", help="run the Muse Spark page check + SAM 3.1")
    p.add_argument("--no-agent", action="store_true", help="skip figure descriptions")
    p.add_argument("--backend", choices=["meta", "baseten"], default=None)
    args = p.parse_args()

    start = _log_lines()
    pdf = pick_pdf(args.pdf)
    doc = ingest(pdf, args.pages)
    regions, notes = detect(doc, use_sam=args.page_check, use_check=args.page_check)
    figures = [r for r in regions if r.kind == "figure"]

    results = {}
    if not args.no_agent:
        for r in figures:
            res = FigureAgent(r, str(pdf), args.backend).run()
            results[r.id] = res
            print(f"  {r.id:9} {r.caption or 'no caption':12} → {res.status.value}"
                  + (f" ({res.escalation_reason})" if res.escalation_reason else ""))

    out_dir = Path("output") / doc.id
    (out_dir / "export_results.json").write_text(
        json.dumps([x.model_dump(mode="json") for x in results.values()], indent=2), encoding="utf-8")
    html_path = out_dir / "accessible.html"
    html_path.write_text(assemble(doc, regions, results), encoding="utf-8")

    calls = [json.loads(line) for line in open(LOG_PATH, encoding="utf-8")][start:] if Path(LOG_PATH).exists() else []
    uncached = [c for c in calls if not c.get("cached")]
    auto = sum(1 for x in results.values() if x.status.value == "auto_approved")
    tables = sum(1 for r in regions if r.kind == "table")
    print(f"\n{len(doc.pages)} pages, {len(figures)} figures ({auto} auto-approved, "
          f"{len(results) - auto} for review), {tables} table regions")
    print(f"Model calls: {len(calls)} ({len(uncached)} uncached)")
    print(f"Wrote {html_path}")


if __name__ == "__main__":
    main()
