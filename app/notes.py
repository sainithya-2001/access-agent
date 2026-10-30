"""Lecture notes (lecture slice): Muse Spark writes structured notes from the transcript, each point
citing the transcript segments it comes from (so each point has a timestamp).

  from app.notes import write_notes
  notes = write_notes(lecture)          # LectureNotes, segment ids checked against the transcript

Text only, temperature 0, cached like every chat() call. Invalid JSON: one repair attempt, then the
error propagates (the caller marks the lecture failed).
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agent.tools import _json, _prompt
from app.llm import chat
from app.models import Lecture, LectureNotes


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def transcript_by_slide(lec: Lecture) -> str:
    segs = {s.id: s for s in lec.transcript.segments}
    out = []
    for sl in lec.slides:
        out.append(f"Slide {sl.index} (on screen {mmss(sl.start)}-{mmss(sl.end)}):")
        out += [f"  [{i}] {mmss(segs[i].start)} {segs[i].text}" for i in sl.segment_ids]
    return "\n".join(out)


def _ask_text(prompt: str, step: str, backend: str | None = None) -> LectureNotes:
    messages = [{"role": "user", "content": prompt}]
    text = chat(messages, backend=backend, step=step, temperature=0)
    try:
        return LectureNotes.model_validate(_json(text))
    except (ValueError, ValidationError) as e:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not valid JSON for the schema ({e}). "
                                                 "Reply again with ONLY the corrected JSON."}]
        return LectureNotes.model_validate(_json(chat(messages, backend=backend, step=step + "_repair", temperature=0)))


def write_notes(lec: Lecture, backend: str | None = None) -> LectureNotes:
    notes = _ask_text(_prompt("lecture_notes", transcript=transcript_by_slide(lec)), "lecture_notes", backend)
    ids = {s.id for s in lec.transcript.segments}
    for sec in notes.sections:
        for p in sec.points:
            p.segments = sorted(set(i for i in p.segments if i in ids))  # cited ids must exist; none left → unsupported
    return notes
