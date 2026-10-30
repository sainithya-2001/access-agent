"""Accessible lecture notes (lecture slice, step 6): one HTML page per lecture, plus WebVTT captions.

  from app.lecture_html import write_lecture_html
  write_lecture_html(lecture)   # → output/lectures/<id>/notes.html, captions.vtt, video.mp4

The page uses the document export's shell (app/assemble.py PAGE and CSS: reading profiles, read aloud,
Blindfold, zoom, Hear the chart) and its figure markup (assemble._figure), so a slide figure looks and
reads like a figure in an exported document. Contents: the video with its captions track; the verified
notes (student_notes: unsupported points left out), one section per slide, each point linking to its
moment in the transcript; where the instructor points at the screen, the slide's figure with its
description if the figure is verified (VERIFIED), else a note that its description is awaiting review;
a slide's other figures at the end of its section; the full transcript (as recognized) with "play from
here" buttons. Blindfold's "Original" is what a screen-reader user gets from the plain video: one image
(the video), then the narration as spoken, with "as you can see here" unexplained.
"""

from __future__ import annotations

import json
import re
import shutil
from html import escape as e
from pathlib import Path

from app import assemble
from app.lecture import OUT_ROOT
from app.models import FigureResult, Lecture, NotePoint, TranscriptSegment
from app.notes import VERIFIED, mmss, slide_figures, student_notes

CUE_CHARS = 84  # about two caption lines

LECTURE_CSS = """
video { display: block; width: 100%; max-width: 46rem; background: #000; border-radius: 6px; }
.lecture-meta, .slide-when, .transcript time { color: var(--muted); }
.slide-when { font-size: 1rem; font-weight: 400; }
ul.notes { padding-left: 1.25rem; }
ul.notes li { margin: 0.5rem 0; }
.kind { font-weight: 700; }
.emphasis { border-left: 4px solid var(--flag-border); padding-left: 0.6rem; }
a.ts { font-size: 0.9em; white-space: nowrap; }
.visual-ref { margin: 1rem 0 1.25rem; padding: 0.75rem 1rem; border: 1px solid var(--line); border-radius: 6px;
  background: var(--panel); }
.visual-ref > p:first-child { margin-top: 0; }
.transcript ol { list-style: none; padding: 0; }
.transcript li { margin: 0.35rem 0; scroll-margin-top: 4rem; }
.transcript li:target { outline: 3px solid var(--link); outline-offset: 2px; }
.transcript time { font-variant-numeric: tabular-nums; margin-right: 0.4rem; }
.play-from { font: inherit; font-size: 0.85em; padding: 0.1rem 0.5rem; margin-right: 0.4rem; color: var(--text);
  background: var(--bg); border: 2px solid var(--text); border-radius: 4px; cursor: pointer; }
"""

SEEK_JS = """
(function () {
  var v = document.getElementById("lecture-video");
  if (!v) return;
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest(".play-from");
    if (!b) return;
    v.currentTime = parseFloat(b.dataset.t);
    v.play();
    v.focus();
  });
})();
"""


def vtt_time(t: float) -> str:
    h, rest = divmod(max(0.0, t), 3600)
    m, s = divmod(rest, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def cues(segments: list[TranscriptSegment]) -> list[tuple[float, float, str]]:
    """Caption cues: a long segment is split at word boundaries into cues of about CUE_CHARS, its time
    shared out by length (the API gives turn times only, no word times)."""
    out = []
    for s in segments:
        words, parts, cur = s.text.split(), [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > CUE_CHARS:
                parts.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            parts.append(cur)
        total = sum(len(p) for p in parts) or 1
        t = s.start
        for p in parts:
            d = (s.end - s.start) * len(p) / total
            out.append((t, t + d, p))
            t += d
    return out


def vtt(segments: list[TranscriptSegment]) -> str:
    lines = ["WEBVTT", ""]
    for i, (a, b, text) in enumerate(cues(segments), 1):
        lines += [str(i), f"{vtt_time(a)} --> {vtt_time(b)}", text, ""]
    return "\n".join(lines)


def _figure_html(fid: str, res: FigureResult, slide: int, review: list) -> str:
    """A slide figure with assemble's markup; an unverified one without its (unchecked) description."""
    region = res.region.model_copy(update={"id": f"s{slide}_{res.region.id}"})
    verified = res.status in VERIFIED
    html = assemble._figure(region, res if verified else None, review)
    return html.replace(f"on page {res.region.page}", f"on slide {slide}")


def _point(p: NotePoint, segs: dict) -> str:
    first = min(p.segments)
    ts = f' <a class="ts" href="#seg-{first}">at {mmss(segs[first].start)}<span class="visually-hidden"> in the transcript</span></a>'
    if p.kind == "definition":
        term = f"{e(p.term)}: " if p.term else ""
        return f'<li><span class="kind">Definition.</span> {term}{e(p.text)}{ts}</li>'
    if p.kind == "emphasis":
        return f'<li class="emphasis"><span class="kind">Emphasized.</span> {e(p.text)}{ts}</li>'
    return f"<li>{e(p.text)}{ts}</li>"


def notes_html(lec: Lecture, con=None, video_src: str = "video.mp4", captions_src: str = "captions.vtt") -> str:
    notes = student_notes(lec.notes)
    segs = {s.id: s for s in lec.transcript.segments}
    figs = slide_figures(lec, con)
    slides = {s.index: s for s in lec.slides}
    review: list = []  # assemble._figure lists unverified figures here; the notes say so in place
    removed = sum(1 for sec in lec.notes.sections for p in sec.points if p.supported is False)
    body, shown = [], set()
    for k, sec in enumerate(notes.sections, 1):
        sl = slides.get(sec.slide)
        when = f' <span class="slide-when">(slide {sl.index}, from {mmss(sl.start)})</span>' if sl else ""
        body.append(f'<section aria-labelledby="sec-{k}"><h2 id="sec-{k}">{e(sec.heading)}{when}</h2>')
        inserts: dict[int | None, list[str]] = {}
        for v in sec.visuals:
            fig = next(((fid, r) for fid, r in figs.get(v.slide, []) if fid == v.figure_id), None)
            said = f'At {mmss(segs[v.segment].start)} the instructor said "{e(v.phrase)}", pointing at the slide.'
            if fig is None:
                block = f'<div class="visual-ref" role="note"><p>{said} The slide has no figure the notes can describe.</p></div>'
            else:
                fid, res = fig
                shown.add(fid)
                if res.status in VERIFIED:
                    block = (f'<div class="visual-ref"><p>{said} This is the figure on the slide:</p>'
                             f"{_figure_html(fid, res, v.slide, review)}</div>")
                else:
                    block = (f'<div class="visual-ref"><p>{said} The description of the figure on that slide is '
                             f"still being checked by staff and will appear here once verified.</p>"
                             f"{_figure_html(fid, res, v.slide, review)}</div>")
            inserts.setdefault(v.after_point, []).append(block)
        body += inserts.get(None, [])
        if sec.points:
            body.append('<ul class="notes">')
            for i, p in enumerate(sec.points):
                body.append(_point(p, segs))
                if i in inserts:
                    body.append("</ul>")
                    body += inserts[i]
                    body.append('<ul class="notes">')
            body.append("</ul>")
        rest = [(fid, r) for fid, r in figs.get(sec.slide, []) if fid not in shown]
        if rest:
            body.append(f"<h3>On slide {sec.slide}</h3>")
            for fid, res in rest:
                shown.add(fid)
                body.append(_figure_html(fid, res, sec.slide, review))
        body.append("</section>")
    body = [re.sub(r'<ul class="notes">\s*</ul>', "", "\n".join(body))]

    t = ['<section class="transcript" aria-labelledby="transcript-h"><h2 id="transcript-h">Transcript</h2>',
         "<p>As recognized from the recording (Muse Voice Transcribe), not corrected. The notes above were "
         "checked against it.</p><ol>"]
    for s in lec.transcript.segments:
        t.append(f'<li id="seg-{s.id}"><button type="button" class="play-from" data-t="{s.start:.2f}">Play'
                 f'<span class="visually-hidden"> the video</span> from {mmss(s.start)}</button>'
                 f'<time datetime="PT{s.start:.1f}S">{mmss(s.start)}</time> {e(s.text)}</li>')
    t.append("</ol></section>")

    checked = ("Every note was checked against the recording" +
               (f"; {removed} note{'s' if removed != 1 else ''} the recording did not support "
                f"{'were' if removed != 1 else 'was'} left out." if removed else "."))
    head = (f'<p class="lecture-meta">Lecture recording, {mmss(lec.transcript.duration)}, '
            f"{len(lec.slides)} slide{'s' if len(lec.slides) != 1 else ''}. {e(checked)}</p>"
            f'<video id="lecture-video" controls preload="metadata" src="{e(video_src)}">'
            f'<track kind="captions" src="{e(captions_src)}" srclang="en" label="English" default>'
            f'<p>Your browser cannot play the video. <a href="{e(video_src)}">Download it</a>.</p></video>')
    original = [{"kind": "Image", "text": ""}] + [{"kind": "Text", "text": s.text} for s in lec.transcript.segments]
    page = assemble.PAGE.format(
        title=e(f"{lec.title} | Lecture notes"), h1=e(lec.title),
        intro=e("Accessible notes, slides and transcript for this lecture."),
        review="", body="\n".join([head] + body + t),
        source=e(f"Lecture recording {Path(lec.video).name}. Slide figures from OpenStax Introductory Statistics 2e, "
                 "licensed under CC BY 4.0."),
        css=assemble.CSS + LECTURE_CSS,
        blindfold_data=json.dumps(original, ensure_ascii=False).replace("</", "<\\/"),
        sentences_js=assemble.SENTENCES_JS.read_text(encoding="utf-8"),
        blindfold_js=assemble.BLINDFOLD_JS.read_text(encoding="utf-8"),
        profiles_js=assemble.PROFILES_JS.read_text(encoding="utf-8"),
        sonify_js=assemble.SONIFY_JS.read_text(encoding="utf-8"))
    return page.replace("</body>", f"<script>{SEEK_JS}</script>\n</body>")


def write_lecture_html(lec: Lecture, con=None, out_dir: str | Path | None = None) -> Path:
    out = Path(out_dir or OUT_ROOT / lec.id)
    out.mkdir(parents=True, exist_ok=True)
    video = out / ("video" + Path(lec.video).suffix)
    if not video.exists() or video.stat().st_size != Path(lec.video).stat().st_size:
        shutil.copy2(lec.video, video)
    (out / "captions.vtt").write_text(vtt(lec.transcript.segments), encoding="utf-8")
    path = out / "notes.html"
    path.write_text(notes_html(lec, con, video.name, "captions.vtt"), encoding="utf-8")
    return path
