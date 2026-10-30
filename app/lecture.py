"""Lecture recordings: slide changes from video frames, transcript segments linked to slides, and the
slides as a PDF the figure pipeline can run on.

  from app.lecture import detect_slides, link_segments, slides_pdf
  slides = detect_slides("data/lectures/test_lecture.mp4", out_dir)    # no model calls
  link_segments(slides, transcript.segments)
  pdf = slides_pdf(slides, out_dir / "slides.pdf")                     # one page per slide

Slide changes: grayscale frames at SAMPLE_FPS, scaled to 160x90; a change where more than CHANGE of
the pixels differ by more than PIXEL (0-255) from the previous frame. A share of changed pixels, not a
mean difference: two white text pages differ by little on average (test clip: mean 6.4 of 255) but in
many places (6.6% of pixels; compression noise moves none). Slides shorter than MIN_SLIDE
seconds are merged into the one before (fades, flicker). Each slide's image is a full-size frame from
its middle. The slides PDF has one image-only page per slide (pixels at 72 dpi), so ingest sees a
scanned page and the page check (Muse Spark + SAM 3.1) finds its figures.

A slides PDF given with the video (same name, .pdf, or --slides): each slide is matched to the PDF
page it shows by image similarity, no model (match_slides): both images trimmed to their content
(pixels darker than WHITE), scaled to MATCH_SIZE, gray, mean-removed; the score is their correlation.
A match needs MATCH_MIN and a lead of MATCH_LEAD over the next page (test clip: 0.99 vs at most 0.53
on Chapter 2's other pages). Matched slides use the real page: crops stop at its caption, captions
come from its text layer, and tables on the pages next to it can supply exact values. Slides with no
match fall back to their frame (image-only page, page check).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pymupdf as fitz

from PIL import Image

from app.models import Lecture, Slide, TranscriptSegment

SAMPLE_FPS = 2
SMALL = (160, 90)
PIXEL = 24
CHANGE = 0.01
MIN_SLIDE = 2.0
WHITE = 235          # gray level: lighter pixels are background (margins, letterboxing)
MATCH_SIZE = (48, 64)
MATCH_DPI = 40
MATCH_MIN = 0.85
MATCH_LEAD = 0.15


def _ffmpeg(*args: str) -> bytes:
    return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", *args],
                          check=True, capture_output=True).stdout


def duration(video: str | Path) -> float:
    reader = imageio_ffmpeg.read_frames(str(video))
    meta = next(reader)
    reader.close()
    return float(meta["duration"])


def frame_diffs(video: str | Path) -> np.ndarray:
    """Share of pixels that changed between consecutive sampled frames; diffs[i] is between sample i and i+1."""
    raw = _ffmpeg("-i", str(video), "-vf", f"fps={SAMPLE_FPS},scale={SMALL[0]}:{SMALL[1]},format=gray",
                  "-f", "rawvideo", "-")
    frames = np.frombuffer(raw, np.uint8).reshape(-1, SMALL[1], SMALL[0]).astype(np.int16)
    return (np.abs(np.diff(frames, axis=0)) > PIXEL).mean(axis=(1, 2))


def change_times(diffs: np.ndarray) -> list[float]:
    """Times (s) where a new slide starts, from the frame differences."""
    times = []
    for i in np.flatnonzero(diffs > CHANGE):
        t = (i + 1) / SAMPLE_FPS
        if times and t - times[-1] < MIN_SLIDE:  # one transition spread over several samples
            continue
        times.append(t)
    return times


def detect_slides(video: str | Path, out_dir: str | Path) -> list[Slide]:
    out_dir = Path(out_dir)
    (out_dir / "slides").mkdir(parents=True, exist_ok=True)
    end = duration(video)
    diffs = frame_diffs(video)
    bounds = [0.0] + [t for t in change_times(diffs) if MIN_SLIDE <= t <= end - MIN_SLIDE] + [end]
    slides = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        img = out_dir / "slides" / f"slide_{i + 1:03d}.png"
        _ffmpeg("-y", "-ss", f"{(a + b) / 2:.3f}", "-i", str(video), "-frames:v", "1", str(img))
        slides.append(Slide(index=i + 1, start=round(a, 3), end=round(b, 3), image_path=img.as_posix()))
    (out_dir / "frame_diffs.json").write_text(json.dumps([round(float(d), 4) for d in diffs]), encoding="utf-8")
    return slides


def link_segments(slides: list[Slide], segments: list[TranscriptSegment]) -> None:
    """Each segment goes to the slide it overlaps most (by time); ties to the earlier slide."""
    for s in slides:
        s.segment_ids = []
    for seg in segments:
        overlap = [max(0.0, min(seg.end, s.end) - max(seg.start, s.start)) for s in slides]
        best = int(np.argmax(overlap)) if max(overlap) > 0 else \
            min(range(len(slides)), key=lambda i: abs(slides[i].start - seg.start))
        slides[best].segment_ids.append(seg.id)


def _signature(img: Image.Image) -> np.ndarray:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    ys, xs = np.nonzero(g < WHITE)
    if len(ys):
        g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    v = np.asarray(Image.fromarray(g.astype(np.uint8)).resize(MATCH_SIZE, Image.BILINEAR), dtype=np.float32).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-9)


def match_slides(slides: list[Slide], pdf_path: str | Path) -> None:
    """Set each slide's source_page (1-based page of the slides PDF) and match score; slides without
    a clear match keep source_page None. No model calls."""
    pdf = fitz.open(str(pdf_path))
    pages = []
    for page in pdf:
        pix = page.get_pixmap(dpi=MATCH_DPI)
        pages.append(_signature(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
    for s in slides:
        f = _signature(Image.open(s.image_path))
        scores = sorted(((float(f @ v), i + 1) for i, v in enumerate(pages)), reverse=True)
        best, page = scores[0]
        lead = best - (scores[1][0] if len(scores) > 1 else -1.0)
        s.match = round(best, 3)
        s.source_page = page if best >= MATCH_MIN and lead >= MATCH_LEAD else None


def slides_pdf(slides: list[Slide], path: str | Path) -> Path:
    """One image-only page per slide, page size = frame size in points."""
    doc = fitz.open()
    for s in slides:
        pix = fitz.Pixmap(s.image_path)
        page = doc.new_page(width=pix.width, height=pix.height)
        page.insert_image(page.rect, filename=s.image_path)
        s.pdf_page = page.number + 1
    doc.save(str(path), garbage=4, deflate=True)
    return Path(path)


# ---------- one lecture, end to end ----------

OUT_ROOT = Path("output/lectures")


def lecture_id(video: str | Path) -> str:
    from app.ingest import _doc_id
    return _doc_id(Path(video))


def process_lecture(video: str | Path, con=None, title: str | None = None, echo: bool = False,
                    slides: str | Path | None = None) -> Lecture:
    """Transcribe, find slides, link segments, run the slides' figures through the figure pipeline
    (pipeline.run into the review store), write and verify the notes (app/notes.py), and write the
    accessible notes page and captions (app/lecture_html.py).
    slides: the lecture's slides PDF (default: the video's name with .pdf, if it exists). Slides
    matched to one of its pages are described from that page (document lecture-<id>-pdf: the matched
    pages plus the pages next to them, for tables); the rest from their frames (document lecture-<id>:
    image-only pages, with the page check).
    Writes output/lectures/<id>/: lecture.json, notes.html, captions.vtt, video. con: a store connection (default store.connect())."""
    from app import pipeline, store
    from app.lecture_html import write_lecture_html
    from app.notes import verify_notes, write_notes
    from app.transcribe import transcribe

    video = Path(video)
    lid = lecture_id(video)
    out = OUT_ROOT / lid
    out.mkdir(parents=True, exist_ok=True)
    transcript = transcribe(video)
    deck = Path(slides) if slides else video.with_suffix(".pdf")
    slides = detect_slides(video, out)
    link_segments(slides, transcript.segments)
    con = con or store.connect()
    keys = []
    if deck.exists():
        match_slides(slides, deck)
        matched = [s for s in slides if s.source_page]
        if matched:
            key = f"lecture-{lid}-pdf"
            total = fitz.open(str(deck)).page_count
            shown = {s.source_page for s in matched}
            spec = ",".join(str(p) for p in sorted({p + d for p in shown for d in (-1, 0, 1) if 1 <= p + d <= total}))
            store.create_document(con, key, lecture_id(deck), str(deck), f"{video.stem} (slides PDF)", spec)
            calls = pipeline.run(con, key, deck, spec, page_check=False, echo=echo, figure_pages=shown)
            for s in matched:
                s.document, s.pdf_page = key, s.source_page
            keys.append(key)
            if echo:
                print(f"Slides PDF {deck.name}: " + ", ".join(f"slide {s.index} = page {s.source_page} ({s.match})"
                                                           for s in matched)
                      + f"; {calls['calls']} model calls ({calls['uncached']} uncached)")
    rest = [s for s in slides if not s.source_page]
    if rest:  # no slides PDF, or slides not in it: their frames, as image-only pages
        pdf = slides_pdf(rest, out / "slides.pdf")
        key = f"lecture-{lid}"  # by the video, not the slides PDF (whose bytes differ per save): one document per lecture
        store.create_document(con, key, lecture_id(pdf), str(pdf), f"{video.stem} (slides)", None)
        calls = pipeline.run(con, key, pdf, None, page_check=True, echo=echo)
        for s in rest:
            s.document = key
        keys.append(key)
        if echo:
            print(f"Slide frames {key}: {calls['calls']} model calls ({calls['uncached']} uncached)")
    lec = Lecture(id=lid, title=title or video.stem.replace("_", " ").capitalize(), video=video.as_posix(),
                  transcript=transcript, slides=slides, slides_document=keys[0] if keys else None)
    lec.notes = verify_notes(lec, write_notes(lec), con)
    if not title:
        lec.title = lec.notes.title
    (out / "lecture.json").write_text(lec.model_dump_json(indent=2), encoding="utf-8")
    write_lecture_html(lec, con, out)
    return lec
