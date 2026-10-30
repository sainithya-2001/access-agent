"""Lecture slice tests on the test clip (scripts/make_test_lecture.py), against its ground truth
data/lectures/test_lecture.json. Model calls are blocked (app/llm.py BLOCKED): everything comes from the
cache that scripts/run_lecture.py filled, so this costs $0 and fails if a step would call a model.

  python scripts/test_lecture.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app import llm, store  # noqa: E402
from app.lecture import OUT_ROOT, detect_slides, lecture_id, link_segments  # noqa: E402
from app.models import Lecture, Status  # noqa: E402
from app.transcribe import transcribe  # noqa: E402

VIDEO = ROOT / "data" / "lectures" / "test_lecture.mp4"
TRUTH = json.loads((ROOT / "data" / "lectures" / "test_lecture.json").read_text(encoding="utf-8"))
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def main() -> None:
    llm.BLOCKED = True
    print("Transcription")
    t = transcribe(VIDEO)
    check(abs(t.duration - TRUTH["duration"]) < 0.5, f"duration {t.duration} s (clip {TRUTH['duration']} s)")
    check(len(t.segments) >= 5 and all(s.start < s.end for s in t.segments)
          and all(a.end <= b.start + 0.05 for a, b in zip(t.segments, t.segments[1:])),
          f"{len(t.segments)} segments, ordered, not overlapping")
    see = next((s for s in t.segments if "as you can see here" in s.text.lower()), None)
    check(see is not None and abs(see.start - TRUTH["see_here"]["approx_time"]) < 3,
          f"'as you can see here' at {see and see.start} s (script estimate {TRUTH['see_here']['approx_time']} s)")

    print("Slides")
    slides = detect_slides(VIDEO, OUT_ROOT / lecture_id(VIDEO))
    want = [(s["start"], s["end"]) for s in TRUTH["slides"]]
    check(len(slides) == len(want), f"{len(slides)} slides (truth {len(want)})")
    check(all(abs(s.start - w[0]) <= 0.5 for s, w in zip(slides, want)),
          f"slide starts {[s.start for s in slides]} vs {[w[0] for w in want]} (±0.5 s)")
    link_segments(slides, t.segments)
    check(sorted(i for s in slides for i in s.segment_ids) == [s.id for s in t.segments],
          "every segment linked to exactly one slide")
    words = [set(re.findall(r"[a-z]{4,}", x["text"].lower())) for x in TRUTH["slides"]]
    wrong = []
    for sl in slides:
        for i in sl.segment_ids:
            seg = set(re.findall(r"[a-z]{4,}", t.segments[i].text.lower()))
            overlap = [len(seg & w) for w in words]
            if seg and max(overlap) > 0 and overlap.index(max(overlap)) != sl.index - 1:
                wrong.append(i)
    check(not wrong, f"each segment linked to the slide whose narration it matches best (wrong: {wrong})")

    print("Slide figures (figure pipeline)")
    lec = Lecture.model_validate_json((OUT_ROOT / lecture_id(VIDEO) / "lecture.json").read_text(encoding="utf-8"))
    figs = store.figures(store.connect(), lec.slides_document)
    pages = sorted(res.region.page for _, res in figs)
    check(pages == [1, 2], f"one figure per slide (pages {pages})")
    done = [res for _, res in figs if res.status in (Status.auto_approved, Status.needs_review)]
    check(len(done) == len(figs), "every slide figure described: " + ", ".join(
        f"slide {r.region.page} {r.status.value}" for _, r in figs))

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
