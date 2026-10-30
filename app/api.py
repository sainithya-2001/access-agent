"""M7: FastAPI backend for the staff review screen.

  uvicorn app.api:app --port 8000        then open http://localhost:8000

Upload a PDF (with a page range) → the pipeline runs in the background → figures appear in the
review queue with status, confidence, issues, and the agent trace → staff approve / edit / reject
(saved with who and when) → export accessible HTML that includes the human edits.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import pymupdf as fitz

from app import pdf, pipeline, sonify, store
from app.agent.orchestrator import NO_SCALE_REASON, FigureAgent
from app.assemble import assemble, printed_page_numbers
from app.detect import _crop
from app.ingest import _doc_id
from app.models import BBox, FigureResult, Status

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "data" / "uploads"
PAGES_RE = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")

app = FastAPI(title="Access Agent review")
con = store.connect()
_lock = threading.Lock()  # one upload at a time keeps API spend predictable (its figures run on a pool)
# Re-runs after a staff recrop: a few at once, not queued behind an upload. _recrops counts each
# figure's recrops, so a slower, older re-run doesn't overwrite a newer one.
_reruns = ThreadPoolExecutor(max_workers=pipeline.WORKERS, thread_name_prefix="rerun")
_recrops: dict[str, int] = {}
_recrops_lock = threading.Lock()


def _doc_or_404(doc_id: str):
    row = store.get_document(con, doc_id)
    if row is None:
        raise HTTPException(404, "No such document")
    return row


def _fig_or_404(fid: str) -> FigureResult:
    res = store.get_figure(con, fid)
    if res is None:
        raise HTTPException(404, "No such figure")
    return res


# ---------- documents ----------

@app.get("/api/documents")
def documents():
    return store.list_documents(con)


def _process(key: str, path: Path, pages: str | None, page_check: bool) -> None:
    with _lock:
        pipeline.run(con, key, path, pages, page_check)


@app.post("/api/documents", status_code=202)
async def upload(tasks: BackgroundTasks, file: UploadFile = File(...), pages: str = Form(""),
                 page_check: bool = Form(False)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(422, "Please upload a PDF file")
    pages = pages.strip() or None
    if pages and not PAGES_RE.match(pages):
        raise HTTPException(422, "Pages should look like 84-97 or 84-91,95")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    path = UPLOADS / Path(file.filename).name
    path.write_bytes(data)
    key = store.document_key(_doc_id(path), pages)
    store.create_document(con, key, _doc_id(path), str(path), path.name, pages)
    tasks.add_task(_process, key, path, pages, page_check)
    return {"id": key}


@app.get("/api/documents/{doc_id}")
def document(doc_id: str):
    row = _doc_or_404(doc_id)
    out = {k: row[k] for k in ("id", "name", "pages", "status", "progress", "error", "updated_at")}
    out["page_list"] = []
    if row["document_json"]:
        doc, regions = store.load_layout(con, doc_id)
        printed = printed_page_numbers(doc)
        out["title"] = doc.title
        out["page_list"] = [{"number": p.number, "printed": printed.get(p.number), "width": p.width,
                             "height": p.height, "scanned": p.is_scanned,
                             "tables": sum(1 for r in regions if r.kind == "table" and r.page == p.number)}
                            for p in doc.pages]
    return out


def reason_code(res: FigureResult) -> str | None:
    """Why a figure is in the queue, as a code the UI turns into plain language and a next step.
    The full technical reason is sent too, for the "Technical details" view."""
    if res.status == Status.pending:
        return "processing"
    if res.status != Status.needs_review:
        return None
    r = res.escalation_reason or res.region.review_reason or ""
    if re.match(r"Series are distinguished by colou?r only", r):  # older runs spelled it "colour"
        return "color_only"
    if r.startswith(NO_SCALE_REASON):
        return "no_scale"
    if res.region.review_reason and r == res.region.review_reason:
        return "unconfirmed_box"
    if r.startswith(("Rewrite limit reached", "Action limit reached")):
        return "checks_still_failing"
    if "processing step failed" in r or "could not be parsed" in r:
        return "error"
    if res.description is None or not res.description.alt_text:
        return "no_description"
    return "agent_unsure"


def _marks(c, ex) -> str | None:
    """What the chart draws its series as ("lines", "bars"), from the classifier's note and the
    title, so the review screen can name the parts. None if it doesn't say."""
    text = " ".join(filter(None, [c and c.reason, ex and ex.title])).lower()
    if re.search(r"\bline|polygon|ogive|time series", text):
        return "lines"
    if re.search(r"\bbar|histogram", text):
        return "bars"
    return None


def _parts(c, ex) -> dict:
    """The chart parts a review reason can name: series (in reading order), what they are drawn as,
    and each axis's label and whether it has numbers."""
    if not ex:
        return {}
    series = list(dict.fromkeys(p.series for p in ex.data if p.series))
    return {"series": series, "marks": _marks(c, ex), "value_axis": ex.value_axis,
            "axes": {"x": {"label": ex.x_axis_label, "numeric": ex.x_axis_numeric_scale},
                     "y": {"label": ex.y_axis_label, "numeric": ex.y_axis_numeric_scale}}}


def _figure_json(fid: str, res: FigureResult) -> dict:
    d, v, c, ex = res.description, res.verification, res.classification, res.extracted
    first = next((t.summary for t in res.trace if t.action == "describe"), None)
    return {
        "id": fid,
        "region_id": res.region.id,
        "page": res.region.page,
        "bbox": res.region.bbox.model_dump(),
        # Changes whenever the crop does, so the browser doesn't show a cached old crop.
        "crop_version": "{}:{:.0f},{:.0f},{:.0f},{:.0f}".format(Path(res.region.crop_path).name, *res.region.bbox.model_dump().values()),
        "caption": res.region.caption,
        "source": res.region.source,
        "status": res.status.value,
        "type": c.type.value if c else None,
        "decorative": bool(c and c.is_decorative),
        "confidence": v.confidence if v else None,
        "passed": v.passed if v else None,
        "issues": [i.model_dump() for i in v.issues] if v else [],
        "reason": res.escalation_reason or res.region.review_reason,
        "reason_code": reason_code(res),
        "parts": _parts(c, ex),
        "alt_text": d.alt_text if d else "",
        "long_description": d.long_description if d else "",
        "latex": d.latex if d else None,
        "data": [p.model_dump() for p in (ex.data if ex else [])],
        "values_estimated": bool(ex and ex.values_estimated),
        "sound": sonify.chart_sound(res),  # "Hear the chart": verified bar/histogram/line/frequency-polygon values only
        "first_description": first,
        "trace": [t.model_dump() for t in res.trace],
        "edits": store.edits_for(con, fid),
    }


@app.get("/api/documents/{doc_id}/figures")
def figures(doc_id: str):
    _doc_or_404(doc_id)
    return [_figure_json(fid, res) for fid, res in store.figures(con, doc_id)]


# ---------- images ----------

def _page(doc_id: str, n: int):
    row = _doc_or_404(doc_id)
    if not row["document_json"]:
        raise HTTPException(404, "Pages are not ready yet")
    doc, _ = store.load_layout(con, doc_id)
    page = next((p for p in doc.pages if p.number == n), None)
    if page is None:
        raise HTTPException(404, "No such page")
    return row, page


@app.get("/api/documents/{doc_id}/pages/{n}/image")
def page_image(doc_id: str, n: int):
    _, page = _page(doc_id, n)
    return FileResponse(page.image_path, media_type="image/png")


@app.get("/api/documents/{doc_id}/pages/{n}/overlay")
def page_overlay(doc_id: str, n: int):
    """The detection overlay drawn by detect.py (boxes colored by detection source)."""
    row, page = _page(doc_id, n)
    path = ROOT / "output" / row["pdf_id"] / "overlays" / f"page_{n:03d}.png"
    if not path.exists():
        raise HTTPException(404, "No overlay for this page")
    return FileResponse(path, media_type="image/png")


@app.get("/api/figures/{fid}/crop")
def crop(fid: str):
    return FileResponse(_fig_or_404(fid).region.crop_path, media_type="image/png")


# ---------- review ----------

class ReviewIn(BaseModel):
    action: str = Field(pattern="^(save|approve|edit|reject)$")
    who: str = Field(min_length=1, max_length=80)
    alt_text: str | None = Field(default=None, max_length=300)
    long_description: str | None = Field(default=None, max_length=8000)
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/figures/{fid}/review")
def review(fid: str, body: ReviewIn):
    _fig_or_404(fid)
    if body.action in ("edit", "save") and not (body.alt_text or "").strip():
        raise HTTPException(422, "Alt text can't be empty; reject the figure or mark it decorative instead")
    res = store.review(con, fid, body.action, body.who.strip(), body.alt_text, body.long_description,
                       body.note)
    return _figure_json(fid, res)


class RecropIn(BaseModel):
    """The new box, in PDF points on the figure's page."""
    who: str = Field(min_length=1, max_length=80)
    x0: float
    y0: float
    x1: float
    y1: float


def _rerun(fid: str, n: int, pdf_path: str, res: FigureResult) -> None:
    key = fid.rsplit(":", 1)[0]
    try:
        tables = [r for r in store.load_layout(con, key)[1] if r.kind == "table"]
        out = FigureAgent(res.region, pdf_path, tables=tables).run()
    except Exception as e:  # FigureAgent.run() doesn't raise, but keep the figure out of 'pending'
        res.status, res.escalation_reason = Status.needs_review, f"Re-run failed: {e.__class__.__name__}"
        out = res
    with _recrops_lock:
        if _recrops.get(fid) == n:  # else staff recropped again meanwhile; that re-run will save
            store.save_figure(con, key, out)


@app.post("/api/figures/{fid}/recrop", status_code=202)
def recrop(fid: str, body: RecropIn):
    """Staff drew a new box around the figure: crop it again and re-run the agent on it."""
    res = _fig_or_404(fid)
    key = fid.rsplit(":", 1)[0]
    row = _doc_or_404(key)
    _, page = _page(key, res.region.page)
    x0, x1 = sorted((max(0.0, body.x0), min(page.width, body.x1)))
    y0, y1 = sorted((max(0.0, body.y0), min(page.height, body.y1)))
    if x1 - x0 < 10 or y1 - y0 < 10:
        raise HTTPException(422, "The box is too small; draw it around the whole figure")
    with _recrops_lock:
        n = _recrops[fid] = _recrops.get(fid, 0) + 1
    # Numbered file: a re-run still reading the previous staff crop keeps its own image.
    path = Path(res.region.crop_path).with_name(f"{res.region.id}_staff{'' if n == 1 else n}.png")
    with pdf.LOCK:
        pdf_page = fitz.open(row["source_path"])[res.region.page - 1]
        crop_path = _crop(pdf_page, fitz.Rect(x0, y0, x1, y1), path)
    region = res.region.model_copy(update={
        "bbox": BBox(x0=x0, y0=y0, x1=x1, y1=y1), "source": "staff", "crop_path": crop_path,
        "review_reason": None,  # a person drew this box: no longer an unconfirmed detection
    })
    res = store.start_recrop(con, fid, body.who.strip(), region)
    _reruns.submit(_rerun, fid, n, row["source_path"], FigureResult(region=region))
    return _figure_json(fid, res)


# ---------- export ----------

def export_html(doc_id: str) -> str:
    doc, regions = store.load_layout(con, doc_id)
    results = {}
    for fid, res in store.figures(con, doc_id):
        if res.status == Status.rejected:
            last = (store.edits_for(con, fid) or [{}])[-1]
            note = f": {last['note']}" if last.get("note") else ""
            res.escalation_reason = f"Rejected by {last.get('who', 'staff')}{note}. Needs a new description"
        results[res.region.id] = res
    return assemble(doc, regions, results)


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str):
    row = _doc_or_404(doc_id)
    if row["status"] != "ready":
        raise HTTPException(409, "The document is still processing")
    name = Path(row["name"]).stem + "-accessible.html"
    return Response(export_html(doc_id), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/documents/{doc_id}/preview", response_class=HTMLResponse)
def preview(doc_id: str):
    _doc_or_404(doc_id)
    return export_html(doc_id)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
