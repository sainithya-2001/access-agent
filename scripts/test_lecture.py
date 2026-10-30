"""Lecture slice tests on the test clip (scripts/make_test_lecture.py), against its ground truth
data/lectures/test_lecture.json. Model calls are blocked (app/llm.py BLOCKED): everything comes from the
cache that scripts/run_lecture.py filled, so this costs $0 and fails if a step would call a model.

  python scripts/test_lecture.py
  python scripts/test_lecture.py --allow-calls    # once, to fill the cache for the planted-error check
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
from app.models import Lecture, NotePoint, Status  # noqa: E402
from app.notes import VISUAL_RE, student_notes, verify_notes  # noqa: E402
from app.transcribe import transcribe  # noqa: E402

VIDEO = ROOT / "data" / "lectures" / "test_lecture.mp4"
TRUTH = json.loads((ROOT / "data" / "lectures" / "test_lecture.json").read_text(encoding="utf-8"))
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def main() -> None:
    llm.BLOCKED = "--allow-calls" not in sys.argv
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

    print("Notes")
    n = lec.notes
    pts = [p for sec in n.sections for p in sec.points]
    check(len(n.sections) == len(slides) and [sec.slide for sec in n.sections] == [s.index for s in slides],
          f"{len(n.sections)} sections, one per slide")
    check(all(p.segments for p in pts), f"{len(pts)} points, each citing at least one segment")
    times = [min(t.segments[i].start for i in p.segments) for p in pts]
    check(times == sorted(times), "points in transcript order (by their first cited segment)")
    kinds = {k: sum(p.kind == k for p in pts) for k in ("key_point", "definition", "emphasis")}
    check(any(p.kind == "definition" and (p.term or "").lower() == "histogram" for p in pts),
          f"histogram defined; kinds {kinds}")
    check(any(p.kind == "emphasis" and "50" in p.text for p in pts), "the emphasis on frequencies adding up to 50 kept")

    print("Notes verifier")
    ok = [p for p in pts if p.supported]
    check(all(p.supported is not None for p in pts), "every point has a verdict")
    check(len(ok) >= 0.85 * len(pts), f"{len(ok)} of {len(pts)} genuine points supported; flagged: "
          + "; ".join(f"'{p.text[:50]}' ({p.check[:60]})" for p in pts if not p.supported))
    shown = [p for sec in student_notes(n).sections for p in sec.points]
    check(len(shown) == len(ok) and all(p.supported for p in shown), "student notes hold only supported points")
    planted = n.model_copy(deep=True)
    seg_of = {k: next(sg.id for sg in t.segments if k in sg.text) for k in ("7%", "bars touch", "separated by gaps")}
    plants = [(0, NotePoint(kind="key_point", text="The 50 and older group was about 12%.", segments=[seg_of["7%"]])),
              (1, NotePoint(kind="key_point", text="Histograms are always symmetric around their tallest bar.",
                            segments=[seg_of["bars touch"]])),
              (0, NotePoint(kind="emphasis", text="The instructor emphasized that the bars in a bar graph must touch.",
                            segments=[seg_of["separated by gaps"]]))]
    for sec, pt in plants:
        planted.sections[sec].points.append(pt)
    verify_notes(lec, planted)
    caught = [pt.supported is False for _, pt in plants]
    check(all(caught), f"planted errors caught: {sum(caught)}/3 (changed number, invented generalization, reversed claim)")
    real = [p for sec in planted.sections for p in sec.points if all(p is not pt for _, pt in plants)]
    check(sum(p.supported is True for p in real) >= 0.85 * len(real), f"{sum(p.supported is True for p in real)} of {len(real)} genuine points still supported alongside the plants")

    print("Spoken references to visuals")
    vis = [v for sec in n.sections for v in sec.visuals]
    see = next((v for v in vis if v.phrase.lower() == "as you can see here"), None)
    check(see is not None and see.slide == 1, f"'as you can see here' linked to slide {see and see.slide}")
    fig = next((r for fid, r in figs if see and fid == see.figure_id), None)
    check(fig is not None and fig.region.page == 1 and fig.status == Status.auto_approved,
          f"its figure is slide 1's, verified ({fig and fig.status.value})")
    check(not VISUAL_RE.search("Today we look at two ways to display data."), "'look at two ways' is not a visual reference")
    check(len(vis) == 1, f"{len(vis)} visual reference(s) in the clip (it has one)")

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
