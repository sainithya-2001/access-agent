"""M4: does the verifier catch planted errors?

For each figure from run_figures.py (output/<doc>/figures.json):
  - CONTROL: the original description (should pass; a failure here is a false alarm)
  - 5 corrupted copies, each with ONE known error planted by the model:
      wrong_number, wrong_trend, missing_label, invented_detail, wrong_label
Each copy goes through tools.verify(). Reports catch rate per error type and false alarms.

Run run_figures.py first. Usage:
  python scripts/test_verifier.py
  python scripts/test_verifier.py --backend baseten
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel  # noqa: E402

from app.agent import tools  # noqa: E402
from app.llm import chat  # noqa: E402
from app.models import Description, FigureResult  # noqa: E402

ERROR_TYPES = {
    "wrong_number": "Change exactly one numeric value to a clearly different but plausible value "
                    "(at least 10% different). Change it in both the long description and the data table.",
    "wrong_trend": "Reverse or misstate the overall pattern in one place (e.g. 'increases' → 'decreases', "
                   "or move the peak to a different point).",
    "missing_label": "Remove all mention of one axis title or one category/series.",
    "invented_detail": "Add one plausible-sounding sentence stating a fact that is not in the figure "
                       "(e.g. a value, a label, or a comparison that is not shown).",
    "wrong_label": "Rename one axis, category, or legend entry to a different plausible name.",
}


class Planted(BaseModel):
    alt_text: str
    long_description: str
    data_table: list[dict] = []
    what_changed: str


def plant(desc: Description, kind: str, backend: str | None) -> tuple[Description, str]:
    prompt = (
        "You are creating a test case for a fact-checker. Modify this image description by introducing "
        f"exactly ONE error: {ERROR_TYPES[kind]} Keep everything else identical.\n\n"
        f"ALT TEXT: {desc.alt_text}\nLONG DESCRIPTION: {desc.long_description}\n"
        f"DATA TABLE: {json.dumps([d.model_dump() for d in desc.data_table])}\n\n"
        'Return ONLY JSON: {"alt_text": ..., "long_description": ..., "data_table": [...], '
        '"what_changed": "<one sentence>"}'
    )
    text = chat([{"role": "user", "content": prompt}], backend=backend, step=f"plant:{kind}")
    p = Planted.model_validate(tools._json(text))
    d = Description(alt_text=p.alt_text[:300], long_description=p.long_description,
                    data_table=[r for r in p.data_table if "label" in r])
    return d, p.what_changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["meta", "baseten"], default=None)
    args = ap.parse_args()

    files = sorted(Path("output").glob("*/figures.json"), key=lambda f: f.stat().st_mtime)
    if not files:
        sys.exit("No figures.json found. Run scripts/run_figures.py first.")
    figures = [FigureResult.model_validate(x) for x in json.loads(files[-1].read_text(encoding="utf-8"))]
    figures = [f for f in figures if f.description and f.extracted]
    print(f"Testing the verifier on {len(figures)} figure(s) from {files[-1]}\n")

    caught, total = defaultdict(int), defaultdict(int)
    false_alarms = 0
    for f in figures:
        r = f.region
        control = tools.verify(r, f.description, f.extracted, args.backend)
        print(f"== {r.id} ({r.caption})")
        print(f"   control: {'PASS' if control.passed else 'FAIL (false alarm)'} ({control.confidence:.2f})")
        if not control.passed:
            false_alarms += 1
            for i in control.issues:
                print(f"     - [{i.source}] {i.kind}: {i.detail}")
        for kind in ERROR_TYPES:
            bad, changed = plant(f.description, kind, args.backend)
            v = tools.verify(r, bad, f.extracted, args.backend)
            total[kind] += 1
            hit = not v.passed
            caught[kind] += hit
            print(f"   {kind:16} {'CAUGHT' if hit else 'MISSED'}  planted: {changed}")
            if hit and v.issues:
                print(f"     verifier: {v.issues[0].kind}: {v.issues[0].detail[:140]}")

    print("\n=== Summary ===")
    all_c, all_t = sum(caught.values()), sum(total.values())
    for kind in ERROR_TYPES:
        print(f"  {kind:16} {caught[kind]}/{total[kind]}")
    print(f"  {'TOTAL':16} {all_c}/{all_t} caught")
    print(f"  false alarms on correct descriptions: {false_alarms}/{len(figures)}")
    print("\nM4 target: catch at least 4 of every 5 planted errors, with few false alarms.")


if __name__ == "__main__":
    main()
