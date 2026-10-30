"""PDF → page images, text blocks, and embedded images (milestone M1).

No model calls here, so this step is free.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf as fitz

from app.models import BBox, Document, EmbeddedImage, Page, TextBlock

RENDER_DPI = 150          # page images for detection/UI
MIN_IMAGE_PT = 40         # ignore embedded images smaller than this (icons, bullets)...
CAPTION_NEAR_PT = 60      # ...unless a "Figure N" caption is within this distance
FIGURE_CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.)\s*\d", re.IGNORECASE)
SCANNED_TEXT_CHARS = 30   # fewer text-layer chars than this on an image-heavy page → scanned


def parse_page_range(spec: str | None, total: int) -> list[int]:
    """'1-3,5' → [1, 2, 3, 5] (1-based). None → all pages."""
    if not spec:
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages.update(range(max(1, int(a)), min(int(b), total) + 1))  # clamped: '1-99999' stays cheap
        else:
            pages.add(int(part))
    return sorted(p for p in pages if 1 <= p <= total)


def _doc_id(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _is_bold(span: dict) -> bool:
    return bool("bold" in span.get("font", "").lower() or (span.get("flags", 0) & 16))


def _line_style(line: dict) -> tuple[float, bool]:
    spans = [s for s in line.get("spans", []) if s["text"].strip()]
    if not spans:
        return (0.0, False)
    size = max(spans, key=lambda s: len(s["text"].strip()))["size"]
    return (round(size, 1), all(_is_bold(s) for s in spans))


def _split_heading_lines(block: dict) -> list[dict]:
    """PyMuPDF sometimes puts a heading and the paragraph under it in one block
    ("Uses of a Time Series Graph Time series graphs are..."). Split off up to two leading
    lines whose style (size, all-bold) differs from the rest."""
    lines = [ln for ln in block.get("lines", []) if any(s["text"].strip() for s in ln.get("spans", []))]
    if len(lines) < 2:
        return [block]
    first = _line_style(lines[0])
    n = 1
    while n < min(len(lines), 2) and _line_style(lines[n]) == first:
        n += 1
    rest = {_line_style(ln) for ln in lines[n:]}
    head_text = " ".join(s["text"].strip() for ln in lines[:n] for s in ln["spans"])
    if not rest or first in rest or len(head_text) > 80 or not (first[1] or first[0] > max(r[0] for r in rest)):
        return [block]

    def part(ls):
        x0 = min(ln["bbox"][0] for ln in ls); y0 = min(ln["bbox"][1] for ln in ls)
        x1 = max(ln["bbox"][2] for ln in ls); y1 = max(ln["bbox"][3] for ln in ls)
        return {"type": 0, "bbox": (x0, y0, x1, y1), "lines": ls}
    return [part(lines[:n]), part(lines[n:])]


def _text_blocks(page: fitz.Page) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    data = page.get_text("dict")
    raw = [b for b in data.get("blocks", []) if b.get("type") == 0]  # 0 = text
    for block in (part for b in raw for part in _split_heading_lines(b)):
        spans = [s for line in block.get("lines", []) for s in line.get("spans", [])]
        text = " ".join(s["text"].strip() for s in spans if s["text"].strip())
        if not text:
            continue
        sizes = [s["size"] for s in spans]
        is_b = [_is_bold(s) for s in spans]
        chars: dict[float, int] = {}
        for s in spans:
            chars[round(s["size"], 1)] = chars.get(round(s["size"], 1), 0) + len(s["text"].strip())
        n = sum(len(s["text"].strip()) for s in spans) or 1
        x0, y0, x1, y1 = block["bbox"]
        blocks.append(TextBlock(
            text=text,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            font_size=max(sizes) if sizes else None,
            is_bold=any(is_b),
            main_size=max(chars, key=chars.get) if chars else None,
            bold_share=round(sum(len(s["text"].strip()) for s, b in zip(spans, is_b) if b) / n, 3),
        ))
    return blocks


def _has_figure_caption(rect: fitz.Rect, text_blocks: list[TextBlock]) -> bool:
    """A 'Figure N' caption just above or below the image, overlapping it horizontally."""
    for b in text_blocks:
        if not FIGURE_CAPTION_RE.match(b.text):
            continue
        if b.bbox.x1 < rect.x0 - 40 or b.bbox.x0 > rect.x1 + 40:
            continue
        if min(abs(b.bbox.y0 - rect.y1), abs(rect.y0 - b.bbox.y1)) <= CAPTION_NEAR_PT:
            return True
    return False


def _embedded_images(doc: fitz.Document, page: fitz.Page, out_dir: Path,
                     text_blocks: list[TextBlock]) -> list[EmbeddedImage]:
    images: list[EmbeddedImage] = []
    for info in page.get_images(full=True):
        xref = info[0]
        for rect in page.get_image_rects(xref):
            if rect.width < MIN_IMAGE_PT or rect.height < MIN_IMAGE_PT:
                # Thin figures (number lines) are under the limit in one direction; keep them
                # only when a figure caption sits right next to them. Icons have no caption.
                if max(rect.width, rect.height) < MIN_IMAGE_PT or not _has_figure_caption(rect, text_blocks):
                    continue
            # A page-sized image is a scan of the whole page, not a figure.
            # Figures on scanned pages are found later by detect.py.
            if rect.width * rect.height > 0.5 * page.rect.width * page.rect.height:
                continue
            # Render the area as it appears on the page (handles masks/colorspaces reliably).
            pix = page.get_pixmap(clip=rect, dpi=RENDER_DPI)
            path = out_dir / f"p{page.number + 1}_img{xref}_{len(images)}.png"
            pix.save(path)
            images.append(EmbeddedImage(
                xref=xref,
                bbox=BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1),
                path=str(path),
            ))
    return images


def _looks_scanned(page: fitz.Page, text_blocks: list[TextBlock]) -> bool:
    chars = sum(len(b.text) for b in text_blocks)
    if chars >= SCANNED_TEXT_CHARS:
        return False
    # Little or no text layer: scanned if an image covers most of the page.
    page_area = page.rect.width * page.rect.height
    for info in page.get_images(full=True):
        for rect in page.get_image_rects(info[0]):
            if rect.width * rect.height > 0.5 * page_area:
                return True
    return chars == 0 and bool(page.get_images())


def ingest(pdf_path: str | Path, pages: str | None = None, out_root: str | Path = "output") -> Document:
    pdf_path = Path(pdf_path)
    doc_id = _doc_id(pdf_path)
    out_dir = Path(out_root) / doc_id
    pages_dir = out_dir / "pages"
    images_dir = out_dir / "images"
    pages_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    pdf = fitz.open(pdf_path)
    result = Document(id=doc_id, source_path=str(pdf_path), title=pdf.metadata.get("title") or None)

    for n in parse_page_range(pages, pdf.page_count):
        page = pdf[n - 1]
        page_img = pages_dir / f"page_{n:03d}.png"
        page.get_pixmap(dpi=RENDER_DPI).save(page_img)
        blocks = _text_blocks(page)
        result.pages.append(Page(
            number=n,
            width=page.rect.width,
            height=page.rect.height,
            image_path=str(page_img),
            text_blocks=blocks,
            embedded_images=_embedded_images(pdf, page, images_dir, blocks),
            is_scanned=_looks_scanned(page, blocks),
        ))

    (out_dir / "document.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
