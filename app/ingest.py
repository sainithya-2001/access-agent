"""PDF → page images, text blocks, and embedded images (milestone M1).

No model calls here, so this step is free.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz

from app.models import BBox, Document, EmbeddedImage, Page, TextBlock

RENDER_DPI = 150          # page images for detection/UI
MIN_IMAGE_PT = 40         # ignore embedded images smaller than this (icons, bullets)
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
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return sorted(p for p in pages if 1 <= p <= total)


def _doc_id(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _text_blocks(page: fitz.Page) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 = text
            continue
        spans = [s for line in block.get("lines", []) for s in line.get("spans", [])]
        text = " ".join(s["text"].strip() for s in spans if s["text"].strip())
        if not text:
            continue
        sizes = [s["size"] for s in spans]
        bold = any("bold" in s.get("font", "").lower() or (s.get("flags", 0) & 16) for s in spans)
        x0, y0, x1, y1 = block["bbox"]
        blocks.append(TextBlock(
            text=text,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            font_size=max(sizes) if sizes else None,
            is_bold=bool(bold),
        ))
    return blocks


def _embedded_images(doc: fitz.Document, page: fitz.Page, out_dir: Path) -> list[EmbeddedImage]:
    images: list[EmbeddedImage] = []
    for info in page.get_images(full=True):
        xref = info[0]
        for rect in page.get_image_rects(xref):
            if rect.width < MIN_IMAGE_PT or rect.height < MIN_IMAGE_PT:
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
            embedded_images=_embedded_images(pdf, page, images_dir),
            is_scanned=_looks_scanned(page, blocks),
        ))

    (out_dir / "document.json").write_text(result.model_dump_json(indent=2))
    return result
