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
import functools
import hashlib
import io
import json
import os
import re
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from app import cache, pdf, stemplot
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
        rect = fitz.Rect(t.bbox)
        # find_tables finds the box reliably but garbles cell text in this book
        # ("32.5%" → "325%\n.", "67,059" → "67059\n,"), so read the cells from word positions.
        rows = _rows_from_text(page, rect) or t.extract()
        if len(rows) >= 2:
            out.append((rect, rows))
    return out


TABLE_CAPTION_RE = re.compile(r"^\s*Table\s*\d", re.IGNORECASE)
CELL_GAP_PT = 6  # words further apart than this on a line are in different cells


def _rows_from_text(pdf_page: fitz.Page, r: fitz.Rect) -> list[list[str | None]] | None:
    """Rebuild table rows from the words inside r, for tables drawn as vectors that
    find_tables missed. Columns come from where text sits across all lines; a line that is one
    unbroken run across several columns (a title, a "Key: 8|0 means 80." row) stays one cell."""
    lines: list[list[tuple]] = []
    words = [w for w in pdf_page.get_text("words")
             if fitz.Rect(w[:4]).intersects(r) and r.y0 <= (w[1] + w[3]) / 2 <= r.y1]
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if lines and abs(lines[-1][0][1] - w[1]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    runs_per_line = []
    for line in lines:
        if min(w[0] for w in line) < r.x0 - 2 or max(w[2] for w in line) > r.x1 + 2:
            continue  # a line running past the box sideways is body text, not a table row
        line.sort(key=lambda w: w[0])
        runs = [[line[0]]]
        for w in line[1:]:
            if w[0] - runs[-1][-1][2] > CELL_GAP_PT:
                runs.append([w])
            else:
                runs[-1].append(w)
        text = " ".join(w[4] for w in line)
        if not TABLE_CAPTION_RE.match(text):  # the caption line is not a row
            runs_per_line.append([(run[0][0], run[-1][2], " ".join(w[4] for w in run)) for run in runs])
    if not runs_per_line:
        return None

    cols: list[list[float]] = []
    for x0, x1, _ in sorted((run for runs in runs_per_line if len(runs) > 1 for run in runs)):
        if cols and x0 <= cols[-1][1] + 2:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    if not cols:
        return [[runs[0][2]] for runs in runs_per_line]

    def col_of(x0: float, x1: float) -> list[int]:
        return [i for i, (c0, c1) in enumerate(cols) if min(x1, c1) - max(x0, c0) > 0]

    rows = []
    for runs in runs_per_line:
        if len(runs) == 1 and len(col_of(runs[0][0], runs[0][1])) > 1:
            rows.append([runs[0][2]])
            continue
        row = [""] * len(cols)
        for x0, x1, text in runs:
            hit = col_of(x0, x1) or [min(range(len(cols)), key=lambda i: abs(cols[i][0] - x0))]
            row[hit[0]] = (row[hit[0]] + " " + text).strip()
        rows.append(row)
    return rows


# ---------- layer 3: vector charts ----------

def _is_caption(text: str) -> bool:
    return bool(CAPTION_RE.match(text) or TABLE_CAPTION_RE.match(text))


def drop_neighbour_captions(box: fitz.Rect, core: fitz.Rect, lines, own: str | None = None) -> fitz.Rect:
    """Pull back the edges of a widened box so it doesn't take in a caption line ("Table 2.17",
    "Figure 2.8") beside the figure's core: that caption belongs to the neighbouring table or figure.
    lines: (rect, text) pairs from the page's text layer. own: the figure's own caption, which may stay;
    if unknown, a caption below core may be its own (OpenStax puts captions below) and stays too.
    The edge stops PAD_PT short of the caption, since crops add that padding, but never cuts into core."""
    box = fitz.Rect(box)
    for br, text in lines:
        if not _is_caption(text) or (own and text.strip().startswith(own.strip()[:12])):
            continue
        if own is None and br.y0 >= core.y1:
            continue
        if not box.intersects(br) or core.intersects(br):
            continue
        if br.y1 <= core.y0:
            box.y0 = min(max(box.y0, br.y1 + PAD_PT + 1), core.y0)
        elif br.y0 >= core.y1:
            box.y1 = max(min(box.y1, br.y0 - PAD_PT - 1), core.y1)
        elif br.x1 <= core.x0:
            box.x0 = min(max(box.x0, br.x1 + PAD_PT + 1), core.x0)
        elif br.x0 >= core.x1:
            box.x1 = max(min(box.x1, br.x0 - PAD_PT - 1), core.x1)
    return box


def _pull_in_labels(box: fitz.Rect, text_blocks) -> fitz.Rect:
    """Grow box to take in short text labels (axis titles, ticks, legend) sitting at its edges.
    Figure captions are not labels. A "Table N" line above the box is the caption of the table
    above it (OpenStax puts captions below), so it isn't pulled in either; one below may be the
    box's own caption, when the box is a table drawn with vector lines."""
    core = fitz.Rect(box)
    box = fitz.Rect(box)
    zone = fitz.Rect(box.x0 - 40, box.y0 - 25, box.x1 + 25, box.y1 + 35)
    for b in text_blocks:
        br = _rect(b.bbox)
        if len(b.text) <= 60 and zone.contains(br) and not CAPTION_RE.match(b.text):
            box |= br
    return drop_neighbour_captions(box, core, [(_rect(b.bbox), b.text) for b in text_blocks])


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
        figs.append(_pull_in_labels(box, text_blocks))
    return figs


# ---------- layer 5: Muse Spark page check ----------

class _Missed(BaseModel):
    x0: float  # normalized 0-1000, top-left origin
    y0: float
    x1: float
    y1: float
    kind: str = "figure"
    label: str = "chart"  # short noun phrase for SAM 3.1, e.g. "bar chart", "number line"
    caption: str | None = None  # e.g. "Figure 2.3" if printed next to it
    note: str = ""


class _Check(BaseModel):
    missed: list[_Missed] = []
    not_figures: list[int] = []  # numbers of drawn boxes that are not figures/tables


CHECK_PROMPT = """You are checking figure detection on a textbook page for accessibility remediation.
Numbered red boxes mark regions already detected (figures and tables).
A "figure" is any chart, graph, plot, diagram, photo, map, or equation image that a blind
reader would need described. Plain paragraphs, headings, and page decorations are not figures.

Return ONLY JSON:
{"missed": [{"x0": int, "y0": int, "x1": int, "y1": int, "kind": "figure"|"table",
             "label": str, "caption": str|null, "note": str}],
 "not_figures": [box numbers that are not figures or tables]}
Coordinates are normalized to 0-1000 on each axis (0,0 = top-left corner of the page,
1000,1000 = bottom-right), whatever the image size.
"label" is a short noun phrase (1-3 words) naming what the figure is, e.g. "bar chart",
"box plot", "number line", "photograph". "caption" is the figure label printed next to it,
e.g. "Figure 2.3", or null. Use empty lists if nothing is wrong."""


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
        {"type": "text", "text": CHECK_PROMPT},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    for attempt in range(2):
        text = chat(messages, step="detect_check", use_cache=(attempt == 0))
        try:
            return _Check.model_validate(_extract_json(text))
        except (ValueError, ValidationError):
            continue
    return _Check()


CAPTION_NEAR_PT = 60
# SAM boxes hug the plot area and leave out axis titles. How to widen them:
#   labels: pull in short text labels from the text layer (digital pages)
#   union:  union with Muse Spark's box      pad: 10% on each side      none: as SAM returned it
# Scanned pages have no text layer, so they use SAM_EXTEND_SCANNED (chosen with --test-check).
SAM_EXTEND_SCANNED = "union"
SAM_PAD = 0.10


def _extend_sam_box(r: fitz.Rect, spark: fitz.Rect, page: Page, mode: str) -> tuple[fitz.Rect, str]:
    if mode == "auto":
        mode = SAM_EXTEND_SCANNED if page.is_scanned else "labels"
    if mode == "labels":
        out = _pull_in_labels(r, page.text_blocks)
    elif mode == "union":
        out = drop_neighbour_captions(r | spark, r, [(_rect(b.bbox), b.text) for b in page.text_blocks])
    elif mode == "pad":
        out = fitz.Rect(r.x0 - SAM_PAD * r.width, r.y0 - SAM_PAD * r.height,
                        r.x1 + SAM_PAD * r.width, r.y1 + SAM_PAD * r.height)
    else:
        return r, "none"
    return out & fitz.Rect(0, 0, page.width, page.height), mode


def _next_to_caption(r: fitz.Rect, page: Page, caption: str | None) -> bool:
    """True if the text layer has this caption ("Figure 2.3", not "Figure 2.30") just above/below r."""
    if not caption or not (m := re.match(r"\s*(?:Figure|Fig\.)\s*(\d+(?:\.\d+)*)", caption, re.IGNORECASE)):
        return False
    label = re.compile(r"^\s*(?:Figure|Fig\.)\s*" + re.escape(m.group(1)) + r"(?![\d.]*\d)", re.IGNORECASE)
    for b in page.text_blocks:
        br = _rect(b.bbox)
        if label.match(b.text) and not (br.x1 < r.x0 - 40 or br.x0 > r.x1 + 40) \
                and min(abs(br.y0 - r.y1), abs(r.y0 - br.y1)) <= CAPTION_NEAR_PT:
            return True
    return False


def _locate_with_sam(page: Page, m: _Missed, spark: fitz.Rect, taken: list[fitz.Rect]):
    """Run SAM 3.1 with Spark's noun phrase; pick the match next to the right caption, else the one
    nearest Spark's box. Returns (rect, confidence, reason) or None if SAM finds nothing usable."""
    from app import sam

    img = Image.open(page.image_path)
    phrase = " ".join(m.label.split()[:4]) or "chart"
    cands = []
    for b in sam.segment_concept(page.image_path, phrase):
        r = fitz.Rect(_px_to_pt(b["x1"], page.width, img.width), _px_to_pt(b["y1"], page.height, img.height),
                      _px_to_pt(b["x2"], page.width, img.width), _px_to_pt(b["y2"], page.height, img.height))
        if max(r.width, r.height) >= MIN_FIG_PT[0] and all(_iou(r, t) < 0.5 for t in taken):
            cands.append((r, b.get("c")))
    if not cands:
        return None

    def dist(r: fitz.Rect) -> float:
        return abs((r.x0 + r.x1 - spark.x0 - spark.x1) / 2) + abs((r.y0 + r.y1 - spark.y0 - spark.y1) / 2)

    captioned = [c for c in cands if _next_to_caption(c[0], page, m.caption)]
    r, conf = min(captioned or cands, key=lambda c: dist(c[0]))
    why = (f"next to its caption {m.caption}" if captioned else "nearest Muse Spark's box") \
        + f" ({len(cands)} SAM match{'es' if len(cands) != 1 else ''})"
    return r, conf, why


# ---------- crops and captions ----------

def _crop(pdf_page: fitz.Page, r: fitz.Rect, path: Path) -> str:
    clip = fitz.Rect(r.x0 - PAD_PT, r.y0 - PAD_PT, r.x1 + PAD_PT, r.y1 + PAD_PT) & pdf_page.rect
    with pdf.LOCK:
        pdf_page.get_pixmap(clip=clip, dpi=CROP_DPI).save(path)
    return str(path)


def _caption_for(r: fitz.Rect, page: Page) -> str | None:
    """Nearest 'Figure N' / 'Table N' caption just below the region (OpenStax puts captions
    below), else the nearest one above. Preferring below matters for tight boxes, where the
    previous figure's caption above can be closer than the figure's own caption."""
    below, above = (None, 60.0), (None, 60.0)
    for b in page.text_blocks:
        if not (CAPTION_RE.match(b.text) or re.match(r"^\s*Table\s*\d", b.text)):
            continue
        br = _rect(b.bbox)
        if br.x1 < r.x0 - 40 or br.x0 > r.x1 + 40:
            continue
        if br.y0 >= r.y1 - 10 and abs(br.y0 - r.y1) < below[1]:
            below = (b.text.strip(), abs(br.y0 - r.y1))
        dist = min(abs(br.y0 - r.y1), abs(r.y0 - br.y1))
        if dist < above[1]:
            above = (b.text.strip(), dist)
    return below[0] or above[0]


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

def detect_page(pdf: fitz.Document, page: Page, out_dir: Path, use_sam: bool = True,
                use_check: bool = True, free_figures: bool = True,
                sam_extend: str = "auto") -> tuple[list[Region], list[str]]:
    """Return (regions, notes). Notes are human-readable detection decisions for the trace.
    free_figures=False skips the embedded-image and vector layers (to test the page check alone).
    sam_extend: how to widen SAM's tight boxes ("auto", "labels", "union", "pad", "none")."""
    pdf_page = pdf[page.number - 1]
    crops = out_dir / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    found: list[tuple[fitz.Rect, str, str, float | None, list | None]] = []  # rect, kind, source, conf, rows
    notes: list[str] = []

    for img in page.embedded_images if free_figures else []:
        found.append((_rect(img.bbox), "figure", "embedded", None, None))

    for rect, rows in _tables(pdf_page):
        found.append((rect, "table", "table", None, rows))

    avoid = [f[0] for f in found]
    for rect in _vector_figures(pdf_page, avoid, page.text_blocks) if free_figures else []:
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

    found = _retag_tables(pdf_page, page, found, notes)
    regions = _to_regions(pdf_page, page, found, crops)

    if use_check:
        check = _spark_check(page, regions)
        if check.not_figures:
            drop = {i - 1 for i in check.not_figures if 1 <= i <= len(regions)}
            for i in sorted(drop):
                notes.append(f"Page check: box {i + 1} ({regions[i].source}) is not a figure → removed")
            found = [f for i, f in enumerate(found) if i not in drop]
        for m in check.missed:
            spark = fitz.Rect(*(min(max(v, 0), 1000) / 1000 * s for v, s in
                                zip((m.x0, m.y0, m.x1, m.y1), (page.width, page.height) * 2))).normalize()
            r, source, conf = spark, "spark_check", None
            what = f"'{m.label}'" + (f" ({m.caption})" if m.caption else "")
            if use_sam:
                try:
                    hit = _locate_with_sam(page, m, spark, [f[0] for f in found])
                except Exception as e:  # SAM is optional; keep Spark's box and send it to review
                    hit = None
                    notes.append(f"SAM 3.1 failed ({e.__class__.__name__}) for {what}")
                if hit:
                    r, conf, why = hit
                    r, how = _extend_sam_box(r, spark, page, sam_extend)
                    source = "spark_check+sam"
                    notes.append(f"Page check: SAM 3.1 placed {what} {why}; box widened by {how}")
                else:
                    notes.append(f"Page check: SAM 3.1 found no {what}; kept Muse Spark's box → human review")
            if all(_iou(r, f[0]) < 0.5 for f in found):
                found.append((r, m.kind if m.kind in ("figure", "table") else "figure", source, conf, None))
                notes.append(f"Page check: missed {m.kind} on page {page.number} ({m.note or 'no note'}) → added")
        regions = _to_regions(pdf_page, page, found, crops)

    # Free signal: captions with no region near them.
    for b in page.text_blocks:
        if CAPTION_RE.match(b.text) and not any(r.caption and r.caption.startswith(b.text.strip()[:12]) for r in regions):
            notes.append(f"Caption '{b.text.strip()[:30]}' on page {page.number} has no detected figure")

    return regions, notes


def _retag_tables(pdf_page, page: Page, found, notes: list[str]):
    """A 'figure' whose nearest caption is 'Table N' is a table drawn with vector lines
    (find_tables missed it): make it a table and read its rows from the text in the box."""
    found = list(found)
    merged: set[int] = set()  # find_tables boxes absorbed into a retagged table
    for i, (rect, kind, source, conf, rows) in enumerate(found):
        cap = _caption_for(rect, page)
        if not (kind == "figure" and cap and TABLE_CAPTION_RE.match(cap)):
            continue
        # find_tables may have caught part of the same table: merge them into one region.
        twin = next((j for j, f in enumerate(found) if f[1] == "table" and j not in merged
                     and f[0].intersects(rect)), None)
        if twin is not None:
            merged.add(twin)
            rect = rect | found[twin][0]
        rows = _rows_from_text(pdf_page, rect)
        found[i] = (rect, "table", source, conf, rows)
        notes.append(f"{source} region near '{cap[:24]}' on page {page.number} is a table → kind=table, "
                     + ("merged with the overlapping find_tables box, " if twin is not None else "")
                     + (f"{len(rows)} rows read from its text" if rows else "no text inside"))
    return [f for j, f in enumerate(found) if j not in merged]


def _to_regions(pdf_page, page: Page, found, crops: Path) -> list[Region]:
    regions = []
    for i, (rect, kind, source, conf, rows) in enumerate(found):
        rid = f"p{page.number}_{kind[0]}{i}"
        caption = _caption_for(rect, page)
        regions.append(Region(
            id=rid, page=page.number, kind=kind, bbox=_bbox(rect),
            crop_path=_crop(pdf_page, rect, crops / f"{rid}.png"),
            source=source, confidence=conf,
            caption=caption,
            nearby_text=_nearby_text(rect, page),
            table_rows=rows,
            stem_leaf=stemplot.parse(rows, caption, pdf_page.get_text()) if kind == "table" else None,
            review_reason=("Box from the Muse Spark page check that SAM 3.1 could not confirm; "
                           "check the crop") if source == "spark_check" else None,
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


# ---------- detection cache ----------
# Detection is most of a cached run's time (find_tables). Each page's regions are cached under the
# PDF hash, the page, the settings, the page-check/SAM model settings and a hash of the code that
# produces them, so a code change starts a fresh cache. Crops are rendered again on every hit.
DETECT_CACHE = Path("cache/detect")
_SOURCES = ("detect.py", "stemplot.py", "ingest.py", "sam.py", "models.py")


@functools.cache
def _code_version() -> str:
    h = hashlib.sha256(fitz.VersionBind.encode())
    for name in _SOURCES:
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()


def _page_key(doc: Document, page: int, *settings) -> str:
    from app import llm
    models = [os.environ.get(v, "") for v in ("LLM_BACKEND", "SPARK_MODEL", "BASETEN_MODEL", "SAM_MODEL")]
    parts = [doc.id, page, *settings, *models, llm.RUN_TAG, _code_version()]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def _cached_page(key: str, pdf_page: fitz.Page, crops: Path) -> tuple[list[Region], list[str]] | None:
    try:
        entry = json.loads((DETECT_CACHE / f"{key}.json").read_text(encoding="utf-8"))
        regions = [Region.model_validate(r) for r in entry["regions"]]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValidationError):
        return None
    crops.mkdir(parents=True, exist_ok=True)
    for r in regions:
        b = r.bbox
        r.crop_path = _crop(pdf_page, fitz.Rect(b.x0, b.y0, b.x1, b.y1), crops / f"{r.id}.png")
    return regions, entry["notes"]


def detect(doc: Document, use_sam: bool = True, use_check: bool = True,
           out_root: str | Path = "output", free_figures: bool = True,
           sam_extend: str = "auto", use_cache: bool = True) -> tuple[list[Region], list[str]]:
    """use_cache=False runs detection again (and stores the new result)."""
    out_dir = Path(out_root) / doc.id
    overlays = out_dir / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    pdf = fitz.open(doc.source_path)
    all_regions, all_notes = [], []
    for page in doc.pages:
        key = _page_key(doc, page.number, use_sam, use_check, free_figures, sam_extend)
        hit = _cached_page(key, pdf[page.number - 1], out_dir / "crops") if use_cache else None
        if hit:
            regions, notes = hit
        else:
            regions, notes = detect_page(pdf, page, out_dir, use_sam, use_check, free_figures, sam_extend)
            cache.write_atomic(DETECT_CACHE / f"{key}.json", json.dumps(
                {"regions": [r.model_dump(mode="json") for r in regions], "notes": notes}))
        draw_overlay(page, regions, overlays / f"page_{page.number:03d}.png")
        all_regions += regions
        all_notes += notes
    (out_dir / "regions.json").write_text(json.dumps(
        {"regions": [r.model_dump() for r in all_regions], "notes": all_notes}, indent=2), encoding="utf-8")
    return all_regions, all_notes
