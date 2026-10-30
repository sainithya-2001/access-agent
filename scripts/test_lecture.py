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
from app.lecture import OUT_ROOT, detect_slides, lecture_id, link_segments, match_slides  # noqa: E402
from app.models import Lecture, NotePoint, Status  # noqa: E402
from app.notes import VISUAL_RE, slide_figures, student_notes, verify_notes  # noqa: E402
from scripts.make_test_lecture import DECK  # noqa: E402
from app.transcribe import transcribe  # noqa: E402

VIDEO = ROOT / "data" / "lectures" / "test_lecture.mp4"
SLIDES_PDF = VIDEO.with_suffix(".pdf")  # scripts/make_test_lecture.py --slides-pdf
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

    print("Slides PDF matching (no model)")
    match_slides(slides, SLIDES_PDF)
    want_pages = [DECK.index(x["page"]) + 1 for x in TRUTH["slides"]]
    check([s.source_page for s in slides] == want_pages,
          f"slides matched to deck pages {[s.source_page for s in slides]} (truth {want_pages}), scores {[s.match for s in slides]}")
    import tempfile
    import pymupdf as fitz
    with tempfile.TemporaryDirectory() as tmp:  # a deck without the shown pages: no match, frames are used
        other = fitz.open()
        src = fitz.open(SLIDES_PDF)
        for i in range(src.page_count):
            if i + 1 not in want_pages:
                other.insert_pdf(src, from_page=i, to_page=i)
        other.save(Path(tmp) / "other.pdf")
        other.close()
        alt = [sl.model_copy() for sl in slides]
        match_slides(alt, Path(tmp) / "other.pdf")
        check(all(sl.source_page is None for sl in alt), f"deck without those pages → no match (best {[sl.match for sl in alt]}): fallback to frames")

    print("Slide figures (figure pipeline)")
    lec = Lecture.model_validate_json((OUT_ROOT / lecture_id(VIDEO) / "lecture.json").read_text(encoding="utf-8"))
    check([s.source_page for s in lec.slides] == want_pages and all(s.document and s.document.endswith("-pdf") for s in lec.slides),
          "the processed lecture used the slides PDF for every slide")
    per_slide = slide_figures(lec, store.connect())
    figs = [f for s in lec.slides for f in per_slide[s.index]]
    check([len(per_slide[s.index]) for s in lec.slides] == [1, 1], f"one figure per slide ({[len(v) for v in per_slide.values()]})")
    check([r.region.caption for _, r in figs] == ["Figure 2.3", "Figure 2.6"], f"captions from the PDF's text layer: {[r.region.caption for _, r in figs]}")
    t23 = figs[0][1].extracted
    check(t23.source is not None and t23.source.caption == "Table 2.9" and [d.value for d in t23.data] == [32.5, 29.5, 16.4, 13.9, 7.1],
          "Figure 2.3 has Table 2.9's exact values (table split over the deck's pages 1-2)")
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
    same = [(a.supported, a.check) == (b.supported, b.check) for a, b in zip(real, pts)]
    check(len(real) == len(pts) and all(same), f"every genuine point's verdict is the same with the plants alongside "
          f"({sum(same)} of {len(pts)}): one call per point, no batching")

    print("Spoken references to visuals")
    vis = [v for sec in n.sections for v in sec.visuals]
    see = next((v for v in vis if v.phrase.lower() == "as you can see here"), None)
    check(see is not None and see.slide == 1, f"'as you can see here' linked to slide {see and see.slide}")
    fig = next((r for fid, r in figs if see and fid == see.figure_id), None)
    check(fig is not None and fig.region.page == lec.slides[0].pdf_page and fig.status == Status.auto_approved,
          f"its figure is slide 1's, verified ({fig and fig.status.value})")
    check(not VISUAL_RE.search("Today we look at two ways to display data."), "'look at two ways' is not a visual reference")
    check(len(vis) == 1, f"{len(vis)} visual reference(s) in the clip (it has one)")

    print("Notes page and captions")
    out = OUT_ROOT / lecture_id(VIDEO)
    html = (out / "notes.html").read_text(encoding="utf-8")
    vtt = (out / "captions.vtt").read_text(encoding="utf-8")
    cue_times = re.findall(r"(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})", vtt)

    def secs(x: str) -> float:
        return int(x[:2]) * 3600 + int(x[3:5]) * 60 + float(x[6:])
    check(vtt.startswith("WEBVTT") and bool(cue_times) and all(secs(a) < secs(b) for a, b in cue_times)
          and all(secs(b) <= secs(c) + 0.001 for (_, b), (c, _) in zip(cue_times, cue_times[1:])),
          f"captions.vtt: {len(cue_times)} cues, ordered, not overlapping")
    longest = max(len(x) for x in vtt.splitlines() if x and "-->" not in x and not x.isdigit() and x != "WEBVTT")
    check(longest <= 90, f"longest cue line {longest} characters")
    check('<track kind="captions" src="captions.vtt"' in html and (out / "video.mp4").exists(), "video with captions track")
    esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")
    check(all(esc(p.text) in html for p in pts if p.supported) and all(esc(p.text) not in html for p in pts if p.supported is False),
          "the page shows every supported point and no unsupported one")
    check(html.count('class="visual-ref"') == len(vis) and "This is the figure on the slide" in html,
          "the verified figure is inserted where the instructor pointed")
    blocks = html.count('class="figure-block"')
    check(blocks == len(figs) and all(f'id="seg-{s.id}"' in html for s in t.segments),
          f"each slide figure once ({blocks}), every transcript segment anchored")

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
