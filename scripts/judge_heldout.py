"""Judge the held-out run's descriptions, after the run, with the dev set's judge (Muse Wonder, the
figure image, temperature 0).

The frozen scorer (run_benchmark.py at tag freeze-v1) judges dev items only, against checked
references; eval/heldout.json records only id, page and caption, so a held-out run there gives status
and cost but no accuracy. This pass adds accuracy without changing that scorer: it calls the same
run_benchmark.judge() with the same prompt (app/agent/prompts/judge.md), with what heldout.json has:
the caption, no type ("not recorded"), no notes, no reference ("(none)"), so the judge grades against
the image and the page text the agent was given. Written before any held-out results existed.

Per item: accurate = the judge says accurate with no factual errors; unsafe = auto-approved and not
accurate. Every held-out item is "judged by model" (no values to check in code). Text tables and
stemplots (no agent, no description) and figures not detected are counted, not judged.

  python scripts/judge_heldout.py --backend meta --rep 1
  -> eval/results/heldout_<backend>_r<rep>_judged.json   (the run's own results file is not changed)

--set dev --only ID runs the same pass on a dev results file, to test the mechanics (dev items are
reduced to id, page and caption, as in heldout.json).
"""

import argparse
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
from scripts.run_benchmark import (JUDGE_MODEL, cost_of, find_region, judge, log_lines, own_calls,  # noqa: E402
                                   results_path, run_pages)


def final_crop(crop_path: str) -> str:
    """The crop the agent last described: recrops chain onto the crop's name (orchestrator
    _recrop_locked), in this run's own crop folder; the newest one is the last."""
    p = Path(crop_path)
    chain = [q for q in p.parent.glob(p.stem + "_recrop_*.png") if not q.stem.endswith("_zoom")]
    return str(max(chain, key=lambda q: q.stat().st_mtime)) if chain else crop_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["meta", "baseten"], required=True)
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--set", choices=["heldout", "dev"], default="heldout", dest="which")
    ap.add_argument("--only", default=None, help="comma-separated item ids (testing on dev results)")
    args = ap.parse_args()

    run = json.loads(results_path(args.which, args.backend, args.rep).read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "eval" / ("heldout.json" if args.which == "heldout" else "benchmark.json"))
                       .read_text(encoding="utf-8"))
    items = {f["id"]: {"id": f["id"], "page": f["page"], "caption": f["caption"],
                       "type": "not recorded", "notes": None, "reference": None} for f in bench["figures"]}
    recs = [r for r in run["items"] if not args.only or r["id"] in args.only.split(",")]

    # The run's regions, re-made from its own folder (ingest and detection are code and cached: no calls).
    out_root = ROOT / "output" / "bench" / f"{args.which}_{args.backend}_r{args.rep}"
    pdf = ROOT / bench.get("source_pdf", "data/samples/introductory-statistics-2e_-_WEB.pdf")
    doc = ingest(pdf, ",".join(str(p) for p in run_pages(list(items.values()), args.which)), out_root=out_root)
    regions, _ = detect(doc, use_sam=False, use_check=False, out_root=out_root)

    out, start = [], len(log_lines())
    for r in recs:
        auto = r["status"] == "auto_approved"
        row = {"id": r["id"], "status": r["status"], "auto_approved": auto}
        if r["status"] == "not_detected" or r.get("agent") is False or r.get("alt_text") is None:
            row.update(judged=False, why="not detected" if r["status"] == "not_detected" else "no description "
                       "(text table or stemplot: no agent)" if r.get("agent") is False else "no description")
            out.append(row)
            print(f"{r['id']:10} {r['status']:14} not judged: {row['why']}")
            continue
        region = next((g for g in regions if g.id == r.get("region_id")), None) or find_region(items[r["id"]], regions)
        region.crop_path = final_crop(region.crop_path)
        res = SimpleNamespace(region=region, description=Description(
            alt_text=r.get("alt_text") or "", long_description=r.get("long_description") or "",
            data_table=[DataPoint(**d) for d in r.get("data_table") or []]))
        j = judge(items[r["id"]], res, r.get("page_text") or "").model_dump()
        accurate = bool(j["accurate"] and not j["factual_errors"])
        row.update(judged=True, crop=Path(region.crop_path).name, judge=j, accurate=accurate,
                   unsafe=auto and not accurate)
        out.append(row)
        print(f"{r['id']:10} {r['status']:14} {'accurate' if accurate else 'NOT accurate'}"
              f"{'  UNSAFE' if row['unsafe'] else ''}  ({len(j['factual_errors'])} factual errors)")

    judged = [x for x in out if x["judged"]]
    summary = {
        "items": len(out), "auto_approved": sum(x["auto_approved"] for x in out),
        "sent_to_review": sum(x["status"] == "needs_review" for x in out),
        "not_detected": sum(x["status"] == "not_detected" for x in out),
        "not_judged": len(out) - len(judged), "judged": len(judged),
        "accurate": sum(x["accurate"] for x in judged),
        "unsafe_approvals": sum(x["unsafe"] for x in judged),
        "unsafe_approvals_judged_by_model": sum(x["unsafe"] for x in judged),
        "auto_approved_not_judged": sum(x["auto_approved"] and not x["judged"] for x in out),
        "judge": cost_of(own_calls(log_lines()[start:])),
    }
    dst = results_path(args.which, args.backend, args.rep).with_name(
        f"{args.which}_{args.backend}_r{args.rep}_judged{'_test' if args.only else ''}.json")
    dst.write_text(json.dumps({"set": args.which, "backend": args.backend, "rep": args.rep,
                               "judge_model": JUDGE_MODEL, "reference": "none (heldout.json: id, page, caption)",
                               "summary": summary, "items": out}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "judge"}))
    print(f"judge calls {summary['judge']['calls']} ({summary['judge']['uncached']} uncached), "
          f"${summary['judge']['usd']:.3f}\nSaved {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
