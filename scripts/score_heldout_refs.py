"""Score the saved held-out results against the checked references (eval/heldout_refs.json), with the
frozen scorer: the functions run_benchmark.py uses for the dev set (judge with references, value
check, invented numbers, score_record, summarize), unchanged at tag freeze-v1. No agent re-run.

1. Fills each reference's region_id from the held-out run (the region its caption matched; both
   backends' runs use the same free-layer detection, so the ids must agree).
2. Per backend: judges each described figure against its reference with the image (Muse Wonder,
   judge.md), then score_record(..., "dev") and summarize(). Scatter-plot points (data.points) have
   no series, so the value check has nothing to compare: those items are judged by the model.

  python scripts/score_heldout_refs.py --rep 1
  -> eval/heldout_refs.json (region_id), eval/results/heldout_<backend>_r<rep>_refs.json
"""

import argparse
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from app.models import DataPoint, Description  # noqa: E402
from scripts.judge_heldout import final_crop  # noqa: E402
from scripts.run_benchmark import (JUDGE_MODEL, cost_of, judge, log_lines, own_calls, results_path,  # noqa: E402
                                   run_pages, score_record, summarize)

REFS = ROOT / "eval" / "heldout_refs.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rep", type=int, default=1)
    args = ap.parse_args()

    refs = json.loads(REFS.read_text(encoding="utf-8"))
    runs = {b: json.loads(results_path("heldout", b, args.rep).read_text(encoding="utf-8")) for b in ("meta", "baseten")}

    # 1. region ids from the runs (matched there by caption on the figure's page)
    for f in refs["figures"]:
        ids = {b: next(r.get("region_id") for r in runs[b]["items"] if r["id"] == f["id"]) for b in runs}
        if len(set(ids.values())) != 1:
            raise SystemExit(f"{f['id']}: the backends' runs matched different regions {ids}")
        f["region_id"] = ids["meta"]
    REFS.write_text(json.dumps(refs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    items = {f["id"]: f for f in refs["figures"]}
    print("region_id filled: " + ", ".join(f"{f['id']}={f['region_id']}" for f in refs["figures"]))

    pdf = ROOT / refs["source_pdf"]
    for b, run in runs.items():
        out_root = ROOT / "output" / "bench" / f"heldout_{b}_r{args.rep}"
        doc = ingest(pdf, ",".join(str(p) for p in run_pages(list(items.values()), "heldout")), out_root=out_root)
        regions = {g.id: g for g in detect(doc, use_sam=False, use_check=False, out_root=out_root)[0]}
        start, results = len(log_lines()), []
        for saved in run["items"]:
            item, rec = items[saved["id"]], copy.deepcopy(saved)
            rec["expected_outcome"] = item["expected_outcome"]
            for k in ("judge", "accurate", "correct_outcome", "unsafe", "scored_by", "values", "mentions",
                      "invented_numbers", "code_accurate", "judge_accurate"):
                rec.pop(k, None)
            if rec["status"] == "not_detected":
                rec.update(correct_outcome=False, unsafe=False)
                results.append(rec)
                continue
            if rec.get("agent") is not False and rec.get("alt_text") is not None:
                region = regions[rec["region_id"]]
                region.crop_path = final_crop(region.crop_path)
                res = SimpleNamespace(region=region, description=Description(
                    alt_text=rec.get("alt_text") or "", long_description=rec.get("long_description") or "",
                    data_table=[DataPoint(**d) for d in rec.get("data_table") or []]))
                rec["judge"] = judge(item, res, rec.get("page_text") or "").model_dump()  # as in run_benchmark.main
            score_record(item, rec, "dev")  # the dev path: references are now recorded
            results.append(rec)
            print(f"{b:8} {rec['id']:10} {rec['status']:14} {rec['scored_by']:16} "
                  f"{'UNSAFE' if rec['unsafe'] else 'ok' if rec['unsafe'] is False else '?'}")
        summary = summarize(results)
        summary["judge"] = cost_of(own_calls(log_lines()[start:]))
        dst = results_path("heldout", b, args.rep).with_name(f"heldout_{b}_r{args.rep}_refs.json")
        dst.write_text(json.dumps({"set": "heldout", "backend": b, "rep": args.rep, "judge_model": JUDGE_MODEL,
                                   "reference": "eval/heldout_refs.json", "summary": summary, "items": results},
                                  indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{b}: " + json.dumps({k: summary[k] for k in ("unsafe_approvals", "unsafe_approvals_code_scored",
                                                            "unsafe_approvals_judge_on_code_scored",
                                                            "unsafe_approvals_judged_by_model", "correct_outcome",
                                                            "auto_approved", "sent_to_review")})
              + f"; judge ${summary['judge']['usd']:.3f}\nSaved {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
