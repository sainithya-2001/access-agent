"""Lecture notes (lecture slice): Muse Spark writes structured notes from the transcript, each point
citing the transcript segments it comes from (so each point has a timestamp); a verifier then checks
every point against those segments and adds the slide's figure where the instructor points at it.

  from app.notes import write_notes, verify_notes, student_notes
  notes = write_notes(lecture)          # LectureNotes, segment ids checked against the transcript
  verify_notes(lecture, notes)          # each point: supported or not (+ why); visual references added
  student_notes(notes)                  # what students see: supported points only

Text only, temperature 0, cached like every chat() call. Invalid JSON: one repair attempt, then the
error propagates (the caller marks the lecture failed).

Verifier: code checks every number in a point against the numbers spoken in its cited segments (digits
or words: "32 and a half" = 32.5); Muse Spark (prompts/lecture_notes_verify.md) judges the claim from
the cited segments alone. A point fails if either says so. Visual references ("as you can see here",
...) are found by code (VISUAL_RE); the slide's figure is attached there, and its description is shown
only if the figure is verified (VERIFIED statuses; app/lecture_html.py).
"""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.agent.tools import _json, _prompt
from app.llm import chat
from app.models import FigureResult, Lecture, LectureNotes, NoteChecks, Status, VisualRef

T = TypeVar("T", bound=BaseModel)

# Spoken pointers at the screen. "here" alone is too common to count.
VISUAL_RE = re.compile(r"\b(as you can see(?: here| on (?:the|this) (?:slide|screen|graph|chart))?|"
                       r"look(?:ing)? here|look(?:ing)? at (?:this|the) (?:slide|graph|chart|figure|screen|plot|diagram)|"
                       r"(?:shown|you see) here|on (?:this|the) slide|this (?:graph|chart|figure|plot|diagram) shows)\b",
                       re.I)
VERIFIED = (Status.auto_approved, Status.approved, Status.edited)

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
         if w != "_"}


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def transcript_by_slide(lec: Lecture) -> str:
    segs = {s.id: s for s in lec.transcript.segments}
    out = []
    for sl in lec.slides:
        out.append(f"Slide {sl.index} (on screen {mmss(sl.start)}-{mmss(sl.end)}):")
        out += [f"  [{i}] {mmss(segs[i].start)} {segs[i].text}" for i in sl.segment_ids]
    return "\n".join(out)


def _ask_text(model: type[T], prompt: str, step: str, backend: str | None = None) -> T:
    messages = [{"role": "user", "content": prompt}]
    text = chat(messages, backend=backend, step=step, temperature=0)
    try:
        return model.model_validate(_json(text))
    except (ValueError, ValidationError) as e:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not valid JSON for the schema ({e}). "
                                                 "Reply again with ONLY the corrected JSON."}]
        return model.model_validate(_json(chat(messages, backend=backend, step=step + "_repair", temperature=0)))


def write_notes(lec: Lecture, backend: str | None = None) -> LectureNotes:
    notes = _ask_text(LectureNotes, _prompt("lecture_notes", transcript=transcript_by_slide(lec)),
                      "lecture_notes", backend)
    ids = {s.id for s in lec.transcript.segments}
    for sec in notes.sections:
        for p in sec.points:
            p.segments = sorted(set(i for i in p.segments if i in ids))  # cited ids must exist; none left → unsupported
    return notes


# ---------- verifier ----------

def spoken_numbers(text: str) -> set[float]:
    """Numbers in transcript text, written as digits or words: "32 and a half" → 32.5, "one half" → 0.5,
    "forty-nine" → 49, "6.5", "16%"."""
    words = re.findall(r"\d+(?:\.\d+)?|[a-z]+", text.lower().replace("-", " "))
    out: set[float] = set()
    i = 0
    while i < len(words):
        w, val = words[i], None
        if re.fullmatch(r"\d+(?:\.\d+)?", w):
            val = float(w)
        elif w in _TENS:
            val = _TENS[w]
            if i + 1 < len(words) and words[i + 1] in _UNITS and _UNITS[words[i + 1]] < 10:
                val += _UNITS[words[i + 1]]
                i += 1
        elif w in _UNITS:
            val = _UNITS[w]
        elif w == "half" and i > 0 and words[i - 1] == "a":  # "a half"
            out.add(0.5)
        if val is not None:
            if words[i + 1:i + 4] == ["and", "a", "half"]:
                val += 0.5
                i += 3
            elif val == 1 and words[i + 1:i + 2] == ["half"]:  # "one half"
                val = 0.5
                i += 1
            out.add(float(val))
        i += 1
    return out


def note_numbers(text: str) -> set[float]:
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))}


def _points_prompt(notes: LectureNotes, segs: dict) -> tuple[str, list]:
    flat, blocks = [], []
    for sec in notes.sections:
        for p in sec.points:
            flat.append(p)
            cited = "\n".join(f"    [{i}] {segs[i].text}" for i in p.segments) or "    (no segments cited)"
            label = f"{p.term}: " if p.term else ""
            blocks.append(f"Note {len(flat) - 1} ({p.kind}): {label}{p.text}\n  Cited segments:\n{cited}")
    return "\n\n".join(blocks), flat


def verify_notes(lec: Lecture, notes: LectureNotes, con=None, backend: str | None = None) -> LectureNotes:
    """Check every point against its cited segments (code: numbers; Muse Spark: the claim), mark it
    supported or not, and add the spoken references to visuals. Unsupported points stay in the returned
    notes, marked (staff see why); student_notes() leaves them out. Returns the same notes object."""
    segs = {s.id: s for s in lec.transcript.segments}
    prompt, flat = _points_prompt(notes, segs)
    checks = {c.id: c for c in _ask_text(NoteChecks, _prompt("lecture_notes_verify", points=prompt),
                                         "lecture_notes_verify", backend).checks}
    for i, p in enumerate(flat):
        if not p.segments:
            p.supported, p.check = False, "cites no transcript segment"
            continue
        said = set().union(*(spoken_numbers(segs[k].text) for k in p.segments))
        missing = sorted(n for n in note_numbers(p.text) if not any(abs(n - m) < 1e-9 for m in said))
        c = checks.get(i)
        if missing:
            p.supported, p.check = False, "numbers not in the cited segments: " + ", ".join(f"{n:g}" for n in missing)
        elif c is None:
            p.supported, p.check = False, "the checker returned no verdict"
        else:
            p.supported, p.check = c.supported, (None if c.supported else c.reason or "not supported")
    add_visuals(lec, notes, con)
    return notes


def slide_figures(lec: Lecture, con=None) -> dict[int, list[tuple[str, FigureResult]]]:
    """Each slide's figures from the review store, in reading order: {slide index: [(figure id, result)]}."""
    from app import store
    con = con or store.connect()
    by_doc: dict[str, dict[int, list]] = {}  # a slide's figures are in its own document (PDF page or frame)
    for key in {s.document or lec.slides_document for s in lec.slides} - {None}:
        for fid, res in store.figures(con, key):
            by_doc.setdefault(key, {}).setdefault(res.region.page, []).append((fid, res))
    return {s.index: by_doc.get(s.document or lec.slides_document, {}).get(s.pdf_page, []) for s in lec.slides}


def add_visuals(lec: Lecture, notes: LectureNotes, con=None) -> None:
    figs = slide_figures(lec, con)
    slide_of = {i: s.index for s in lec.slides for i in s.segment_ids}
    for sec in notes.sections:
        sec.visuals = []
    for seg in lec.transcript.segments:
        m = VISUAL_RE.search(seg.text)
        if not m or seg.id not in slide_of:
            continue
        slide = slide_of[seg.id]
        sec = next((x for x in notes.sections if x.slide == slide), None) or \
            next((x for x in notes.sections if any(seg.id in p.segments for p in x.points)), None)
        if sec is None:
            continue
        after = max((k for k, p in enumerate(sec.points) if seg.id in p.segments), default=None)  # after the segment's last point
        fig = (figs.get(slide) or [(None, None)])[0][0]
        sec.visuals.append(VisualRef(segment=seg.id, phrase=m.group(0), slide=slide, figure_id=fig, after_point=after))


def student_notes(notes: LectureNotes) -> LectureNotes:
    """What students see: only supported points (visual refs re-pointed to the kept points)."""
    out = notes.model_copy(deep=True)
    for sec in out.sections:
        keep = [k for k, p in enumerate(sec.points) if p.supported is not False]
        for v in sec.visuals:
            v.after_point = keep.index(v.after_point) if v.after_point in keep else \
                next((keep.index(k) for k in reversed(keep) if v.after_point is not None and k < v.after_point), None)
        sec.points = [sec.points[k] for k in keep]
    return out
