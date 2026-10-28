"""Figure and table detection (milestone M2).

Layers, cheapest first:
  1. Embedded images (from ingest)                                  free
  2. Text tables via PyMuPDF find_tables                            free
  3. Vector-drawn charts from the PDF's drawing commands             free
  4. SAM 3.1 concept prompts, for scanned pages                     Meta API
  5. Muse Spark page check: did we miss a figure? false positives?  Meta API / Baseten
     Missed figures it reports are refined to a precise box by SAM 3.1 (box prompt).

Captions ("Figure 2.10") are used as a free signal: a caption with no region near it
means something was probably missed.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from app.ingest import RENDER_DPI
from app.models import BBox, Document, Page, Region

CROP_DPI = 200
PAD_PT = 6                 # padding around crops so axis labels aren't cut
MIN_FIG_PT = (60, 40)      # min width, height for a vector cluster to count as a figure
MIN_DRAWINGS = 6           # a chart is many drawing commands, not one box
CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.)\s*\d+(\.\d+)*", re.IGNORECASE)
SAM_CONCEPTS = ["chart", "diagram", "photograph"]


# ---------- geometry helpers ----------

def _rect(b: BBox) -> fitz.Rect:
    return fitz.Rect(b.x0, b.y0, b.x1, b.y1)


def _bbox(r: fitz.Rect) -> BBox:
    return BBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1)


def _iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = fitz.Rect(a) & b
    if inter.is_empty:
        return 0.0
    ia = inter.width * inter.height
    return ia / (a.width * a.height + b.width * b.height - ia)


def _overlap_frac(a: fitz.Rect, b: fitz.Rect) -> float:
    """Fraction of a covered by b."""
    inter = fitz.Rect(a) & b
    if inter.is_empty or a.is_empty:
        return 0.0
    return (inter.width * inter.height) / (a.width * a.height)


def _px_to_pt(v: float, page_pt: float, img_px: float) -> float:
    return v * page_pt / img_px


# ---------- layer 2: tables ----------

def _tables(page: fitz.Page) -> list[tuple[fitz.Rect, list[list[str | None]]]]:
    out = []
    try:
        found = page.find_tables()
    except Exception:
        return out
    for t in found.tables:
        rows = t.extract()
        if len(rows) >= 2:
            out.append((fitz.Rect(t.bbox), rows))
    return out


# ---------- layer 3: vector charts ----------

def _vector_figures(page: fitz.Page, avoid: list[fitz.Rect], text_blocks) -> list[fitz.Rect]:
    page_area = page.rect.width * page.rect.height
    drawings = [d for d in page.get_drawings() if d.get("rect") is not None]
    # Thin lines have zero-height/width rects, which never intersect anything; give them 1pt.
    rects = []
    for d in drawings:
        r = fitz.Rect(d["rect"])
        rects.append(fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1))

    keep: list[fitz.Rect] = []
    for i, r in enumerate(rects):
        if r.width * r.height > 0.25 * page_area:
            continue  # page backgrounds
        if r.height < 2 and r.width > 0.6 * page.rect.width:
            continue  # full-width rules
        if any(_overlap_frac(r, a) > 0.8 for a in avoid):
            continue  # part of a table or an already-known image
        # "Container" boxes (shaded example boxes) hold other drawings or paragraphs.
        contains = sum(1 for j, o in enumerate(rects) if j != i and r.contains(o))
        holds_paragraph = any(len(b.text) > 120 and r.contains(_rect(b.bbox)) for b in text_blocks)
        if contains >= 3 or holds_paragraph:
            continue
        keep.append(r)

    # Merge nearby drawings into clusters.
    clusters: list[list[fitz.Rect]] = []
    for r in keep:
        grown = fitz.Rect(r.x0 - 8, r.y0 - 8, r.x1 + 8, r.y1 + 8)
        hits = [c for c in clusters if any(grown.intersects(x) for x in c)]
        merged = [r] + [x for c in hits for x in c]
        clusters = [c for c in clusters if c not in hits] + [merged]

    figs = []
    for c in clusters:
        if len(c) < MIN_DRAWINGS:
            continue
        box = fitz.Rect(c[0])
        for x in c[1:]:
            box |= x
        if box.width < MIN_FIG_PT[0] or box.height < MIN_FIG_PT[1]:
            continue
        # Pull in short text labels (axis labels, ticks, legend) that sit at the edges.
        zone = fitz.Rect(box.x0 - 40, box.y0 - 25, box.x1 + 25, box.y1 + 35)
        for b in text_blocks:
            br = _rect(b.bbox)
            if len(b.text) <= 60 and zone.contains(br) and not CAPTION_RE.match(b.text):
                box |= br
        figs.append(box)
    return figs


# ---------- layer 5: Muse Spark page check ----------

class _Missed(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "figure"
    note: str = ""


class _Check(BaseModel):
    missed: list[_Missed] = []
    not_figures: list[int] = []  # numbers of drawn boxes that are not figures/tables


CHECK_PROMPT = """You are checking figure detection on a textbook page for accessibility remediation.
Numbered red boxes mark regions already detected (figures and tables).
A "figure" is any chart, graph, plot, diagram, photo, map, or equation image that a blind
reader would need described. Plain paragraphs, headings, and page decorations are not figures.

Return ONLY JSON:
{"missed": [{"x0": int, "y0": int, "x1": int, "y1": int, "kind": "figure"|"table", "note": str}],
 "not_figures": [box numbers that are not figures or tables]}
Coordinates are pixels in this image ({w}x{h}), top-left origin. Use empty lists if nothing is wrong."""


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


def _spark_check(page: Page, regions: list[Region]) -> _Check:
    from app.llm import chat  # imported lazily so free layers work without API keys

    img = Image.open(page.image_path).convert("RGB")
    sx, sy = img.width / page.width, img.height / page.height
    d = ImageDraw.Draw(img)
    for i, r in enumerate(regions, 1):
        b = r.bbox
        d.rectangle((b.x0 * sx, b.y0 * sy, b.x1 * sx, b.y1 * sy), outline="red", width=4)
        d.text((b.x0 * sx + 6, b.y0 * sy + 4), str(i), fill="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": CHECK_PROMPT.replace("{w}", str(img.width)).replace("{h}", str(img.height))},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    for attempt in range(2):
        text = chat(messages, step="detect_check", use_cache=(attempt == 0))
        try:
            return _Check.model_validate(_extract_json(text))
        except (ValueError, ValidationError):
            continue
    return _Check()


# ---------- crops and captions ----------

def _crop(pdf_page: fitz.Page, r: fitz.Rect, path: Path) -> str:
    clip = fitz.Rect(r.x0 - PAD_PT, r.y0 - PAD_PT, r.x1 + PAD_PT, r.y1 + PAD_PT) & pdf_page.rect
    pdf_page.get_pixmap(clip=clip, dpi=CROP_DPI).save(path)
    return str(path)


def _caption_for(r: fitz.Rect, page: Page) -> str | None:
    """Nearest 'Figure N' / 'Table N' caption just below (or above) the region."""
    best, best_dist = None, 60.0
    for b in page.text_blocks:
        if not (CAPTION_RE.match(b.text) or re.match(r"^\s*Table\s*\d", b.text)):
            continue
        br = _rect(b.bbox)
        if br.x1 < r.x0 - 40 or br.x0 > r.x1 + 40:
            continue
        dist = min(abs(br.y0 - r.y1), abs(r.y0 - br.y1))
        if dist < best_dist:
            best, best_dist = b.text.strip(), dist
    return best


def _nearby_text(r: fitz.Rect, page: Page, limit: int = 600) -> str:
    """Paragraph text closest to the region, used later as description context."""
    blocks = sorted(page.text_blocks, key=lambda b: min(abs(b.bbox.y1 - r.y0), abs(b.bbox.y0 - r.y1)))
    out = ""
    for b in blocks:
        if len(out) + len(b.text) > limit:
            break
        out += b.text + "\n"
    return out.strip()


# ---------- main ----------

def detect_page(pdf: fitz.Document, page: Page, out_dir: Path,
                use_sam: bool = True, use_check: bool = True) -> tuple[list[Region], list[str]]:
    """Return (regions, notes). Notes are human-readable detection decisions for the trace."""
    pdf_page = pdf[page.number - 1]
    crops = out_dir / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    found: list[tuple[fitz.Rect, str, str, float | None, list | None]] = []  # rect, kind, source, conf, rows
    notes: list[str] = []

    for img in page.embedded_images:
        found.append((_rect(img.bbox), "figure", "embedded", None, None))

    for rect, rows in _tables(pdf_page):
        found.append((rect, "table", "table", None, rows))

    avoid = [f[0] for f in found]
    for rect in _vector_figures(pdf_page, avoid, page.text_blocks):
        found.append((rect, "figure", "vector", None, None))

    if use_sam and page.is_scanned:
        img = Image.open(page.image_path)
        from app import sam
        for concept in SAM_CONCEPTS:
            for b in sam.segment_concept(page.image_path, concept):
                r = fitz.Rect(_px_to_pt(b["x1"], page.width, img.width), _px_to_pt(b["y1"], page.height, img.height),
                              _px_to_pt(b["x2"], page.width, img.width), _px_to_pt(b["y2"], page.height, img.height))
                if all(_iou(r, f[0]) < 0.5 for f in found):
                    found.append((r, "figure", "sam", b.get("c"), None))
                    notes.append(f"SAM 3.1 found a '{concept}' on scanned page {page.number}")

    regions = _to_regions(pdf_page, page, found, crops)

    if use_check:
        check = _spark_check(page, regions)
        if check.not_figures:
            drop = {i - 1 for i in check.not_figures if 1 <= i <= len(regions)}
            for i in sorted(drop):
                notes.append(f"Page check: box {i + 1} ({regions[i].source}) is not a figure → removed")
            found = [f for i, f in enumerate(found) if i not in drop]
        if check.missed:
            img = Image.open(page.image_path)
            for m in check.missed:
                box = (m.x0, m.y0, m.x1, m.y1)
                source = "spark_check"
                if use_sam:
                    try:
                        from app import sam
                        refined = sam.refine_box(page.image_path, *box, img.width, img.height)
                        if refined:
                            box = (refined["x1"], refined["y1"], refined["x2"], refined["y2"])
                            source = "spark_check+sam"
                    except Exception as e:  # SAM box prompts are optional; keep Spark's box
                        notes.append(f"SAM refine failed ({e.__class__.__name__}); using Muse Spark's box")
                r = fitz.Rect(_px_to_pt(box[0], page.width, img.width), _px_to_pt(box[1], page.height, img.height),
                              _px_to_pt(box[2], page.width, img.width), _px_to_pt(box[3], page.height, img.height))
                if all(_iou(r, f[0]) < 0.5 for f in found):
                    found.append((r, m.kind if m.kind in ("figure", "table") else "figure", source, None, None))
                    notes.append(f"Page check: missed {m.kind} on page {page.number} ({m.note or 'no note'}) → added")
        regions = _to_regions(pdf_page, page, found, crops)

    # Free signal: captions with no region near them.
    for b in page.text_blocks:
        if CAPTION_RE.match(b.text) and not any(r.caption and r.caption.startswith(b.text.strip()[:12]) for r in regions):
            notes.append(f"Caption '{b.text.strip()[:30]}' on page {page.number} has no detected figure")

    return regions, notes


def _to_regions(pdf_page, page: Page, found, crops: Path) -> list[Region]:
    regions = []
    for i, (rect, kind, source, conf, rows) in enumerate(found):
        rid = f"p{page.number}_{kind[0]}{i}"
        regions.append(Region(
            id=rid, page=page.number, kind=kind, bbox=_bbox(rect),
            crop_path=_crop(pdf_page, rect, crops / f"{rid}.png"),
            source=source, confidence=conf,
            caption=_caption_for(rect, page),
            nearby_text=_nearby_text(rect, page),
            table_rows=rows,
        ))
    return regions


COLORS = {"embedded": "blue", "vector": "green", "table": "orange",
          "sam": "purple", "spark_check": "red", "spark_check+sam": "magenta"}


def draw_overlay(page: Page, regions: list[Region], path: Path) -> None:
    img = Image.open(page.image_path).convert("RGB")
    sx, sy = img.width / page.width, img.height / page.height
    d = ImageDraw.Draw(img)
    for r in regions:
        b = r.bbox
        color = COLORS.get(r.source, "black")
        d.rectangle((b.x0 * sx, b.y0 * sy, b.x1 * sx, b.y1 * sy), outline=color, width=4)
        d.text((b.x0 * sx + 6, b.y0 * sy + 4), f"{r.id} [{r.source}]", fill=color)
    img.save(path)


def detect(doc: Document, use_sam: bool = True, use_check: bool = True,
           out_root: str | Path = "output") -> tuple[list[Region], list[str]]:
    out_dir = Path(out_root) / doc.id
    overlays = out_dir / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    pdf = fitz.open(doc.source_path)
    all_regions, all_notes = [], []
    for page in doc.pages:
        regions, notes = detect_page(pdf, page, out_dir, use_sam, use_check)
        draw_overlay(page, regions, overlays / f"page_{page.number:03d}.png")
        all_regions += regions
        all_notes += notes
    (out_dir / "regions.json").write_text(json.dumps(
        {"regions": [r.model_dump() for r in all_regions], "notes": all_notes}, indent=2), encoding="utf-8")
    return all_regions, all_notes
