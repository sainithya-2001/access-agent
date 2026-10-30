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
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pymupdf as fitz

from app.models import Lecture, Slide, TranscriptSegment

SAMPLE_FPS = 2
SMALL = (160, 90)
PIXEL = 24
CHANGE = 0.01
MIN_SLIDE = 2.0


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


def process_lecture(video: str | Path, con=None, title: str | None = None, echo: bool = False) -> Lecture:
    """Transcribe, find slides, link segments, run the slides' figures through the figure pipeline
    (pipeline.run into the review store, with the page check: slide frames are image-only pages), and
    write the notes (app/notes.py).
    Writes output/lectures/<id>/lecture.json. con: a store connection (default store.connect())."""
    from app import pipeline, store
    from app.notes import write_notes
    from app.transcribe import transcribe

    video = Path(video)
    lid = lecture_id(video)
    out = OUT_ROOT / lid
    out.mkdir(parents=True, exist_ok=True)
    transcript = transcribe(video)
    slides = detect_slides(video, out)
    link_segments(slides, transcript.segments)
    pdf = slides_pdf(slides, out / "slides.pdf")
    con = con or store.connect()
    key = store.document_key(lecture_id(pdf), None)
    store.create_document(con, key, lecture_id(pdf), str(pdf), f"{video.stem} (slides)", None)
    calls = pipeline.run(con, key, pdf, None, page_check=True, echo=echo)
    if echo:
        print(f"Slides document {key}: {calls['calls']} model calls ({calls['uncached']} uncached)")
    lec = Lecture(id=lid, title=title or video.stem.replace("_", " ").capitalize(), video=video.as_posix(),
                  transcript=transcript, slides=slides, slides_document=key)
    lec.notes = write_notes(lec)
    if not title:
        lec.title = lec.notes.title
    (out / "lecture.json").write_text(lec.model_dump_json(indent=2), encoding="utf-8")
    return lec
