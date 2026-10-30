"""M6: assemble accessible HTML from ingest, detection, and agent results.

No model calls here. Steps:
  1. Drop running headers/footers; keep the printed page number for page markers.
  2. Headings from font size and bold (one h1, no skipped levels).
  3. Text blocks, figures, and tables in reading order (page, then top to bottom).
  4. Figures: <figure> with alt text, <figcaption>, long description linked by aria-describedby,
     a data table for charts (values from extract_values), MathML for equations, and a visible
     staff flag when the figure still needs review.
  5. Tables: <caption> + <th scope="col">; a table split across pages is merged into one.
     Stemplots also get a sentence listing every data value (expanded in code).
"""

from __future__ import annotations

import base64
import html
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz

from app import pdf, sonify, stemplot
from app.agent import tools
from app.models import Document, FigureResult, Region, Status, TextBlock

MARGIN_PT = 45            # running headers/footers live in the top/bottom margin
HEADING_SIZE_RATIO = 1.2  # a block this much larger than body text is a heading
LABEL_MAX_CHARS = 60      # all-bold short block ("EXAMPLE 2.5", "Solution") → heading
FIG_CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.|Table)\s*\d+(\.\d+)*", re.IGNORECASE)
CHILD_LABELS = {"problem", "solution", "answer"}  # nest under the open "EXAMPLE n" heading
NUMERIC_RE = re.compile(r"^[-–+$]?[\d,.]+%?$|^[\d.,]+\s*[–-]\s*[\d.,]+%?$")

e = html.escape


# ---------- 1. running headers and footers ----------

def _furniture(doc: Document) -> tuple[set[tuple[int, int]], dict[int, str]]:
    """(page, block index) pairs to drop, and printed page numbers by PDF page."""
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\d+", "#", t)).strip().lower()

    margin = {}
    for p in doc.pages:
        for i, b in enumerate(p.text_blocks):
            if b.bbox.y1 < MARGIN_PT or b.bbox.y0 > p.height - MARGIN_PT:
                margin[(p.number, i)] = b.text.strip()
    repeats = Counter(norm(t) for t in margin.values())
    drop, printed = set(), {}
    for (page, i), t in margin.items():
        # Running heads: repeated lines, the book's URL footer, or a line whose page number is set
        # apart by wide spacing ("2.1 • Stem-and-Leaf Graphs     73" appears once per section).
        if "openstax.org" in t.lower() or re.fullmatch(r"\d+", t) or repeats[norm(t)] >= 2 \
                or re.search(r"\S\s{3,}\d+$|^\d+\s{3,}\S", t):
            drop.add((page, i))
            num = re.search(r"\s(\d+)$", t) or re.match(r"^(\d+)(\s|$)", t)
            if num and page not in printed:
                printed[page] = num.group(1)
    return drop, printed



def printed_page_numbers(doc: Document) -> dict[int, str]:
    """Printed (book) page number by PDF page, read from the running header/footer."""
    return _furniture(doc)[1]

# ---------- 2. headings ----------

def _body_size(doc: Document, drop) -> float:
    sizes = Counter()
    for p in doc.pages:
        for i, b in enumerate(p.text_blocks):
            if (p.number, i) not in drop and b.main_size:
                sizes[round(b.main_size, 1)] += len(b.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heading_levels(doc: Document, drop, body: float) -> dict[float, int]:
    """Larger sizes → h2, h3, ... (h1 is the document title)."""
    big = sorted({round(b.main_size, 1) for p in doc.pages for i, b in enumerate(p.text_blocks)
                  if (p.number, i) not in drop and b.main_size and b.main_size >= body * HEADING_SIZE_RATIO},
                 reverse=True)
    return {s: lvl for lvl, s in enumerate(big, start=2)}


def _heading_kind(b: TextBlock, levels: dict[float, int], body: float) -> tuple[str, int] | None:
    """("size", tier level) for a larger-font heading, ("label", 0) for a short all-bold line
    such as "EXAMPLE 2.5" or "Solution", None for body text."""
    if b.main_size and round(b.main_size, 1) in levels:
        return "size", levels[round(b.main_size, 1)]
    if (b.bold_share >= 0.95 and len(b.text) <= LABEL_MAX_CHARS and not FIG_CAPTION_RE.match(b.text)
            and b.main_size and abs(b.main_size - body) < 0.5):
        return "label", 0
    return None


# ---------- text helpers ----------

def _clean(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,;:!?)\]]|\.(?!\d))", r"\1", t)   # "Table 2.9 ." → "Table 2.9.", but keep " .5"
    return re.sub(r"([(\[])\s+", r"\1", t)


def _inside(b: TextBlock, r: Region) -> bool:
    br = fitz.Rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)
    rr = fitz.Rect(r.bbox.x0 - 2, r.bbox.y0 - 2, r.bbox.x1 + 2, r.bbox.y1 + 2)
    inter = br & rr
    return not inter.is_empty and inter.width * inter.height >= 0.5 * br.width * br.height


def _caption_label(caption: str | None) -> str | None:
    m = FIG_CAPTION_RE.match(caption or "")
    return m.group(0).strip() if m else None


def _is_caption_of(b: TextBlock, r: Region) -> bool:
    label = _caption_label(r.caption)
    if not label or not re.match(re.escape(label) + r"(?![\d.]*\d)", b.text.strip()):
        return False
    return min(abs(b.bbox.y0 - r.bbox.y1), abs(r.bbox.y0 - b.bbox.y1)) <= 60


def _num(v: float) -> str:
    return f"{v:,.10g}" if abs(v) >= 10000 else f"{v:.10g}"


# ---------- 4. figures ----------

def _img_src(path: str) -> str:
    data = Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def hear_chart(sound: dict | None, name: str) -> str:
    """The "Hear the chart" button (web/sonify.js), only for charts app/sonify.py offers sound for.
    Hidden until the script finds Web Audio."""
    if not sound:
        return ""
    return (f'<div class="hear-chart" hidden><button type="button" class="hear-chart-btn" '
            f'data-sound="{e(json.dumps(sound))}"><span class="hear-chart-label">Hear the chart</span>'
            f'<span class="visually-hidden">: {e(name)}</span></button> '
            f'<span class="hear-chart-status" role="status"></span></div>')


def _wrap_open(label: str) -> str:
    """A wide table scrolls in its own box on a narrow screen (focusable, so it scrolls by keyboard)
    instead of making the whole page scroll sideways."""
    return f'<div class="table-wrap" role="region" tabindex="0" aria-label="{e(label)}">'


def _chart_table(res: FigureResult, fid: str, caption: str) -> str:
    ex = res.extracted
    pts = [d for d in ex.data if d.value is not None] if ex else []
    if not pts or tools.no_numeric_scale(ex):  # values off an axis with no numbers are guesses
        return ""
    series = list(dict.fromkeys(d.series or "" for d in pts))
    labels = list(dict.fromkeys(d.label for d in pts))
    value = {(d.label, d.series or ""): d.value for d in pts}
    first = e(ex.x_axis_label or "Category")
    heads = [e(s) if s else e(ex.y_axis_label or "Value") for s in series]
    note = " Values are read from the chart's axis, so they are approximate." if ex.values_estimated else ""
    if res.status not in (Status.auto_approved, Status.approved, Status.edited):
        note += " Not yet checked by staff."
    out = [_wrap_open(f"Data for {caption}"), f'<table class="data-table" id="{fid}-data">',
           f"<caption>Data for {e(caption)}.{e(note)}</caption>",
           "<thead><tr>" + f'<th scope="col">{first}</th>' + "".join(f'<th scope="col">{h}</th>' for h in heads)
           + "</tr></thead><tbody>"]
    for lab in labels:
        cells = "".join(f"<td>{_num(value[(lab, s)]) if (lab, s) in value else ''}</td>" for s in series)
        out.append(f'<tr><th scope="row">{e(lab)}</th>{cells}</tr>')
    out.append("</tbody></table></div>")
    return "\n".join(out)


def _mathml(latex: str) -> str | None:
    try:
        from latex2mathml.converter import convert
        return convert(latex, display="block")
    except Exception:
        return None


def _figure(r: Region, res: FigureResult | None, review: list[tuple[str, str]]) -> str:
    fid = f"fig-{r.id}"
    region = res.region if res else r          # the agent may have re-cropped
    caption = _clean(r.caption) if r.caption else None
    desc = res.description if res else None
    kind = res.classification.type.value if res and res.classification else None
    name = _caption_label(caption) or f"{'Equation' if kind == 'equation' else 'Figure'} on page {r.page}"
    decorative = bool(res and res.classification and res.classification.is_decorative) or \
        (desc is not None and not desc.alt_text and res is not None and res.status == Status.auto_approved)

    needs_review = res is None or res.status not in (Status.auto_approved, Status.approved, Status.edited)
    reason = (res.escalation_reason if res else None) or ("No description yet" if res is None else "")
    parts = [f'<div class="figure-block" id="{fid}">']
    if needs_review:
        conf = f" (verifier confidence {res.verification.confidence:.2f})" if res and res.verification else ""
        parts.append(f'<p class="review-flag" role="note"><strong>Staff review needed:</strong> '
                     f"{e(reason or 'The description was not verified')}{e(conf)}</p>")
        review.append((fid, f"{name}: {reason or 'not verified'}"))

    parts.append("<figure>")
    math = _mathml(desc.latex) if desc and desc.latex and kind == "equation" else None
    has_long = bool(desc and desc.long_description and not decorative)
    if math:
        # Focusable so keyboard users can scroll a long equation on a narrow screen.
        parts.append(f'<div class="math" tabindex="0" role="group" aria-label="{e(name)}">{math}</div>')
    elif decorative:
        parts.append(f'<img src="{_img_src(region.crop_path)}" alt="">')
    else:
        alt = desc.alt_text if desc and desc.alt_text else f"{name} (not yet described)"
        described = f' aria-describedby="{fid}-desc"' if has_long else ""
        parts.append(f'<img src="{_img_src(region.crop_path)}" alt="{e(alt)}"{described}>')
    if caption:
        parts.append(f"<figcaption>{e(caption)}</figcaption>")
    parts.append("</figure>")
    if not decorative and not math:  # shown only in the Low vision profile (web/profiles.js)
        parts.append(f'<button type="button" class="zoom-btn" data-zoom="{fid}">Zoom figure'
                     f'<span class="visually-hidden">: {e(name)}</span></button>')

    if has_long:
        paras = "".join(f"<p>{e(_clean(p))}</p>" for p in desc.long_description.split("\n\n") if p.strip())
        label = "Description" if caption else f"Description of {e(name)}"  # no caption: name it here
        parts.append(f'<div class="long-desc" id="{fid}-desc"><p class="long-desc-label">{label}</p>{paras}</div>')
    if res and kind == "chart" and not decorative:
        parts.append(_chart_table(res, fid, name))
        parts.append(hear_chart(sonify.chart_sound(res), name))
    parts.append("</div>")
    return "\n".join(p for p in parts if p)


# ---------- 5. tables ----------

def _merge_split_tables(items: list) -> list:
    """A table continued on the next page has the same 'Table N' caption: merge the rows
    (dropping the repeated header/title rows) into the first part."""
    out = []
    for it in items:
        if it[0] == "table":
            prev = next((o for o in reversed(out) if o[0] == "table"), None)
            r = it[2]
            if prev and _caption_label(prev[2].caption) and _caption_label(prev[2].caption) == _caption_label(r.caption) \
                    and r.page - prev[2].page in (0, 1):
                head = prev[2].table_rows or []
                rows = [row for row in (r.table_rows or []) if row not in head[:2]]
                prev[2].table_rows = head + rows
                prev[3].append(r.page)
                continue
        out.append(it)
    return out


def _is_numeric(c: str | None) -> bool:
    return bool(c and NUMERIC_RE.match(c.strip()))


def _table(r: Region, pages: list[int], context: str) -> str:
    rows = [[_clean(c or "") for c in row] for row in (r.table_rows or [])]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    ncols = max(len(row) for row in rows)
    title = None
    if len([c for c in rows[0] if c]) == 1 and (ncols > 1 or len(rows) > 1):
        title = next(c for c in rows[0] if c)
        rows = rows[1:]
    header = None
    if rows and sum(_is_numeric(c) for c in rows[0] if c) < len([c for c in rows[0] if c]) / 2 \
            and len(rows) > 1 and len(rows[0]) == ncols:
        header, rows = rows[0], rows[1:]

    caption = _clean(r.caption) if r.caption else f"Table on page {r.page}"
    if title and title.lower() not in caption.lower():
        caption = f"{caption}: {title}"
    if len(pages) > 1:
        caption += f" (continues across PDF pages {pages[0]}–{pages[-1]})"
    out = [_wrap_open(caption), f'<table id="tbl-{r.id}"><caption>{e(caption)}</caption>']
    if header:
        out.append("<thead><tr>" + "".join(f'<th scope="col">{e(c)}</th>' for c in header) + "</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) == 1 and ncols > 1 and len(row) != ncols or (len(row) == 1 and ncols > 1):
            out.append(f'<tr><td colspan="{ncols}">{e(filled[0])}</td></tr>')
        else:
            out.append("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row + [""] * (ncols - len(row))) + "</tr>")
    out.append("</tbody></table></div>")

    # Stemplots: re-parse after merging, so values from every page are included.
    sl = stemplot.parse(r.table_rows, r.caption, context)
    if sl:
        for name, vals in sl.series.items():
            label = "" if name == "values" else f" ({name})"
            assumed = " Each leaf is taken as the ones digit." if sl.unit_source == "assumed" else ""
            out.append(f'<p class="stem-values">The {len(vals)} data values in this stem-and-leaf plot{e(label)} '
                       f"are: {e(', '.join(_num(v) for v in vals))}.{e(assumed)}</p>")
    return "\n".join(out)


# ---------- 3. reading order + document ----------

def assemble(doc: Document, regions: list[Region], results: dict[str, FigureResult]) -> str:
    drop, printed = _furniture(doc)
    body = _body_size(doc, drop)
    levels = _heading_levels(doc, drop, body)
    page_text = {p.number: " ".join(b.text for b in p.text_blocks) for p in doc.pages}

    items: list = []  # (kind, page, payload, extra) in reading order
    for p in doc.pages:
        regs = [r for r in regions if r.page == p.number]
        entries = []
        for i, b in enumerate(p.text_blocks):
            if (p.number, i) in drop or any(_inside(b, r) or _is_caption_of(b, r) for r in regs):
                continue
            entries.append((b.bbox.y0, b.bbox.x0, ("text", p.number, b, None)))
        for r in regs:
            entries.append((r.bbox.y0, r.bbox.x0, (r.kind if r.kind == "table" else "figure", p.number, r, [r.page])))
        items.append(("page", p.number, None, None))
        items += [x[2] for x in sorted(entries, key=lambda x: (round(x[0]), x[1]))]
    items = _merge_split_tables(items)

    review: list[tuple[str, str]] = []
    out: list[str] = []
    section_level, prev_text = 1, None  # level of the last font-size heading (1 = only the h1)
    label_level: int | None = None      # level of the open "EXAMPLE n" / "TRY IT n" label
    list_x0 = 0.0                       # left edge of the last bullet block
    para: dict | None = None  # {"text": str, "markers": [(position in text, marker html)]}

    def flush():
        """Emit the open paragraph. A page marker held for a paragraph that did not continue on
        the next page goes after it, on its own line."""
        nonlocal para
        if para:
            held = para["markers"] if para.get("open") else []
            markers = [] if held else para["markers"]
            text, pos, chunks = para["text"], 0, []
            for at, marker in markers:
                chunks += [e(_clean(text[pos:at])), marker]
                pos = at
            chunks.append(e(_clean(text[pos:])))
            out.append("<p>" + " ".join(c for c in chunks if c) + "</p>")
            out.extend(f'<p class="page-marker-line">{m}</p>' for _, m in held)
            para = None

    for kind, page, obj, extra in items:
        if kind == "page":
            marker = f"Page {printed[page]} (PDF page {page})" if page in printed else f"PDF page {page}"
            marker_html = f'<span class="page-marker" id="page-{page}">{e(marker)}</span>'
            # A paragraph running across the page break may continue: hold the marker inline.
            if para and not re.search(r"[.?!:]$", para["text"].strip()):
                para["markers"].append((len(para["text"]), marker_html))
                para["open"] = True
                prev_text = None
                continue
            flush()
            out.append(f'<p class="page-marker-line">{marker_html}</p>')
            prev_text = None
            continue
        if kind != "text":
            flush()
            prev_text = None
            if kind == "figure":
                out.append(_figure(obj, results.get(obj.id), review))
            else:
                out.append(_table(obj, extra, " ".join(page_text.get(n, "") for n in extra)))
            continue

        b: TextBlock = obj
        heading = _heading_kind(b, levels, body)
        if heading:
            flush()
            kind_, tier = heading
            # Size headings keep their tier but never skip a level; bold labels ("EXAMPLE 2.5")
            # sit one level below the current section heading, and "Problem"/"Solution" one
            # level below the label they belong to.
            if kind_ == "size":
                level = section_level = min(tier, section_level + 1)
                label_level = None
            elif b.text.strip().lower() in CHILD_LABELS and label_level:
                level = label_level + 1
            else:
                level = label_level = section_level + 1
            out.append(f"<h{level}>{e(_clean(b.text))}</h{level}>")
            prev_text = None
            continue
        text = b.text.strip()
        # A block indented past the last bullet continues that list item ("Xres = 1.").
        continues_item = bool(out) and out[-1].startswith("<ul>") and para is None and b.bbox.x0 > list_x0 + 3
        if "•" in text or continues_item:
            # Bullets: "• a • b", also mid-block ("ENTER. • Arrow down..."): text before the first
            # bullet continues the previous list item.
            flush()
            lead, *items = text.split("•")
            if items:
                list_x0 = b.bbox.x0
            if lead.strip():
                if out and out[-1].startswith("<ul>") and (continues_item or items):
                    out[-1] = out[-1][:-len("</li></ul>")] + " " + e(_clean(lead)) + "</li></ul>"
                else:
                    out.append(f"<p>{e(_clean(lead))}</p>")
            lis = "".join(f"<li>{e(_clean(x))}</li>" for x in items if x.strip())
            if lis:
                if out and out[-1].startswith("<ul>"):
                    out[-1] = out[-1][:-len("</ul>")] + lis + "</ul>"
                else:
                    out.append(f"<ul>{lis}</ul>")
            prev_text = None
            continue
        # Same paragraph if the block continues right below the previous one at the same indent,
        # or picks up a sentence (or a data list) left open at the bottom of the previous page.
        same_page = para and prev_text is not None and b.bbox.y0 - prev_text.bbox.y1 < 4 \
            and abs(b.bbox.x0 - prev_text.bbox.x0) < 15
        next_page = para and para.get("open") and (
            text[:1].islower() or para["text"].rstrip()[-1:] in ",;–-"
            or (para["text"].rstrip()[-1:].isdigit() and text[:1].isdigit()))
        if same_page or next_page:
            para["text"] += " " + text
            para["open"] = False
        else:
            flush()
            para = {"text": text, "markers": []}
        prev_text = b
    flush()

    first, last = doc.pages[0].number, doc.pages[-1].number
    book = _book_title(doc)
    chapter = _chapter_title(doc) or book
    shown = sorted(printed[p.number] for p in doc.pages if p.number in printed)
    pdf_span = _ranges([p.number for p in doc.pages])
    # Both numbering systems: the book's printed page numbers, and the PDF's (what staff selected).
    span = f"pages {_ranges([int(x) for x in shown])} (PDF pages {pdf_span})" if shown else f"PDF pages {pdf_span}"
    review_html = ""
    if review:
        lis = "".join(f'<li><a href="#{fid}">{e(text)}</a></li>' for fid, text in review)
        review_html = (f'<section class="review-summary" aria-labelledby="review-heading">'
                       f'<h2 id="review-heading">Staff review: {len(review)} item{"s" if len(review) != 1 else ""} '
                       f"to check</h2><ul>{lis}</ul></section>")
    return PAGE.format(
        title=e(f"{chapter}, {span} | {book}"),
        h1=e(chapter),
        intro=e(f"Accessible version of {span} of {book}."),
        review=review_html,
        body="\n".join(out),
        source=e(f"{book}, OpenStax, licensed under CC BY 4.0. PDF pages {first}–{last}."),
        css=CSS,
        **_blindfold(doc, regions),
    )


# ---------- 6. Blindfold mode ----------

SENTENCES_JS = Path(__file__).resolve().parents[1] / "web" / "sentences.js"
BLINDFOLD_JS = Path(__file__).resolve().parents[1] / "web" / "blindfold.js"
PROFILES_JS = Path(__file__).resolve().parents[1] / "web" / "profiles.js"
SONIFY_JS = Path(__file__).resolve().parents[1] / "web" / "sonify.js"


def original_reading(doc: Document, regions: list[Region]) -> list[dict]:
    """What a screen reader gets from the untreated PDF, for Blindfold mode's "Original" version:
    every text block in the PDF's order, running headers included, no headings; an embedded image is
    only "Image"; the text inside a table or chart runs together as one stream; a scanned page is
    one image. Blocks inside a figure or table carry its region id (Blindfold starts at a figure)."""
    out = []
    for p in doc.pages:
        if p.is_scanned:
            out.append({"kind": "Image", "text": "", "page": p.number})
            continue
        regs = [r for r in regions if r.page == p.number]
        items: list[list] = []  # [kind, text, y0, region]
        for b in p.text_blocks:
            text = re.sub(r"\s+", " ", b.text).strip()
            if not text:
                continue
            region = next((r for r in regs if _inside(b, r)), None)
            if region is not None and items and items[-1][3] is region:
                items[-1][1] += " " + text
            else:
                items.append(["Text", text, b.bbox.y0, region])
        for img in p.embedded_images:
            at = next((i for i, it in enumerate(items) if it[2] >= img.bbox.y0), len(items))
            ir = fitz.Rect(img.bbox.x0, img.bbox.y0, img.bbox.x1, img.bbox.y1)
            region = next((r for r in regs if r.kind == "figure" and (ir & fitz.Rect(
                r.bbox.x0 - 2, r.bbox.y0 - 2, r.bbox.x1 + 2, r.bbox.y1 + 2)).get_area() >= 0.5 * ir.get_area()), None)
            items.insert(at, ["Image", "", img.bbox.y0, region])
        out += [{"kind": k, "text": t, "page": p.number} | ({"region": r.id} if r is not None else {})
                for k, t, _, r in items]
    return out


def _blindfold(doc: Document, regions: list[Region]) -> dict:
    data = json.dumps(original_reading(doc, regions), ensure_ascii=False).replace("</", "<\\/")
    return {"blindfold_data": data, "sentences_js": SENTENCES_JS.read_text(encoding="utf-8"),
            "blindfold_js": BLINDFOLD_JS.read_text(encoding="utf-8"),
            "profiles_js": PROFILES_JS.read_text(encoding="utf-8"),
            "sonify_js": SONIFY_JS.read_text(encoding="utf-8")}


def _ranges(nums: list[int]) -> str:
    nums = sorted(set(nums))
    out, start = [], None
    for i, n in enumerate(nums):
        start = n if start is None else start
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            out.append(f"{start}–{n}" if n != start else str(n))
            start = None
    return ", ".join(out)


def _book_title(doc: Document) -> str:
    return doc.title or Path(doc.source_path).stem


def _chapter_title(doc: Document) -> str | None:
    try:
        with pdf.LOCK:
            toc = fitz.open(doc.source_path).get_toc()
    except Exception:
        return None
    first = doc.pages[0].number
    chapters = [t for t in toc if t[0] == 1 and t[2] <= first]
    if not chapters:
        return None
    m = re.match(r"Chapter\s+(\d+)\s+(.*)", chapters[-1][1])
    return f"Chapter {m.group(1)}: {m.group(2)}" if m else chapters[-1][1]


CSS = """
:root { --text: #1a1a1a; --bg: #ffffff; --muted: #4a4a4a; --link: #0b5cad; --line: #8a8a8a;
        --flag-bg: #fff4ce; --flag-border: #8a6100; --panel: #f3f5f8; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 1.125rem/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
main { max-width: 46rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
footer { max-width: 46rem; margin: 0 auto; padding: 1rem; color: var(--muted); font-size: 0.95rem;
         border-top: 1px solid var(--line); }
h1, h2, h3, h4 { line-height: 1.25; margin: 2rem 0 0.75rem; }
h1 { font-size: 2rem; margin-top: 0.5rem; }
h2 { font-size: 1.5rem; }
h3 { font-size: 1.25rem; }
h4 { font-size: 1.1rem; }
a { color: var(--link); }
.skip-link { position: absolute; left: 0.5rem; top: -3rem; background: var(--text); color: var(--bg);
             padding: 0.5rem 1rem; z-index: 10; }
.skip-link:focus { top: 0.5rem; }
:focus-visible { outline: 3px solid var(--link); outline-offset: 2px; }
.page-marker-line { margin: 2rem 0 1rem; border-top: 1px dashed var(--line); padding-top: 0.25rem; }
.page-marker { font-size: 0.85rem; color: var(--muted); }
figure { margin: 1.5rem 0 0.5rem; }
figure img { max-width: 100%; height: auto; border: 1px solid #d0d0d0; }
figcaption { font-weight: 600; margin-top: 0.4rem; }
.figure-block { margin: 1.5rem 0; }
.long-desc { background: var(--panel); padding: 0.75rem 1rem; border-left: 4px solid var(--line); }
.long-desc-label { font-weight: 600; margin: 0 0 0.25rem; }
.long-desc p { margin: 0.25rem 0; }
.review-flag { background: var(--flag-bg); border: 2px solid var(--flag-border); padding: 0.5rem 0.75rem; }
.review-summary { background: var(--flag-bg); border: 2px solid var(--flag-border); padding: 0 1rem 0.5rem;
                  margin: 1rem 0 2rem; }
.review-summary h2 { margin-top: 0.75rem; font-size: 1.2rem; }
table { border-collapse: collapse; margin: 1rem 0; font-size: 1rem; }
caption { text-align: left; font-weight: 600; padding-bottom: 0.4rem; }
th, td { border: 1px solid var(--line); padding: 0.3rem 0.6rem; text-align: left; vertical-align: top; }
thead th { background: var(--panel); }
.data-table caption { font-weight: 400; }
.stem-values { font-size: 1rem; }
.math { overflow-x: auto; font-size: 1.25rem; }
.blindfold-launch button { font: inherit; font-size: 1rem; padding: 0.5rem 1rem; color: var(--bg);
  background: var(--text); border: 2px solid var(--text); border-radius: 6px; cursor: pointer; }
.table-wrap { max-width: 100%; overflow-x: auto; }
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
/* ---------- reading profiles (web/profiles.js): presentation only ---------- */
.profile-bar { background: var(--panel); border-bottom: 1px solid var(--line); padding: 0.5rem 1rem; }
.profile-bar fieldset { max-width: 46rem; margin: 0 auto; border: 0; padding: 0; display: flex; flex-wrap: wrap;
  align-items: center; gap: 0.25rem 1.25rem; }
.profile-bar legend { float: left; font-weight: 600; margin-right: 0.5rem; padding: 0; }
.profile-bar label { display: inline-flex; align-items: center; gap: 0.35rem; cursor: pointer; }
.profile-bar input[type=radio] { width: 1.1rem; height: 1.1rem; margin: 0; }
.profile-hint { max-width: 46rem; margin: 0.25rem auto 0; font-size: 0.9rem; color: var(--muted); }
.hear-chart { margin: 0.75rem 0 0; display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; }
.hear-chart[hidden] { display: none; }
.hear-chart-btn { font: inherit; font-size: 1rem; color: var(--text); background: var(--bg);
  border: 2px solid var(--text); border-radius: 6px; padding: 0.35rem 0.8rem; cursor: pointer; }
.hear-chart-status { color: var(--muted); }
.ra-bar, .zoom-btn { display: none; }
.ra-bar { max-width: 46rem; margin: 0.5rem auto 0; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.ra-bar button, .ra-bar select, .zoom-btn { font: inherit; font-size: 1rem; color: var(--text); background: var(--bg);
  border: 2px solid var(--text); border-radius: 6px; padding: 0.35rem 0.8rem; cursor: pointer; }
.ra-label { font-weight: 600; }
.ra-status { margin: 0; }
.zoom-dialog { width: min(96vw, 80rem); height: 92vh; max-width: none; max-height: none; padding: 0;
  border: 3px solid var(--text); background: var(--bg); color: var(--text); }
.zoom-dialog[open] { display: flex; flex-direction: column; }
.zoom-dialog::backdrop { background: rgb(0 0 0 / 0.85); }
.zoom-head { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line); }
.zoom-head h2 { margin: 0 auto 0 0; font-size: 1.25rem; }
.zoom-head button { font: inherit; font-size: 1rem; color: var(--text); background: var(--bg);
  border: 2px solid var(--text); border-radius: 6px; padding: 0.35rem 0.8rem; cursor: pointer; }
.zoom-view { flex: 1; overflow: auto; background: #fff; }
.zoom-view img { display: block; max-width: none; height: auto; }

/* Low vision: large reflowing text, high contrast, zoomable figures. */
html[data-profile="low-vision"] { font-size: 150%; --text: #ffffff; --bg: #000000; --muted: #e8e8e8;
  --link: #ffe14d; --line: #d0d0d0; --flag-bg: #000000; --flag-border: #ffe14d; --panel: #1f1f1f; }
html[data-profile="low-vision"] body { line-height: 1.6; }
html[data-profile="low-vision"] a { text-decoration-thickness: 0.12em; text-underline-offset: 0.15em; }
html[data-profile="low-vision"] figure img { width: 100%; background: #fff; border: 2px solid #fff; }
html[data-profile="low-vision"] table { width: 100%; table-layout: fixed; }
html[data-profile="low-vision"] th, html[data-profile="low-vision"] td { overflow-wrap: anywhere; }
html[data-profile="low-vision"] .zoom-btn { display: inline-block; margin-top: 0.5rem; }
html[data-profile="low-vision"] .math { font-size: 1.5rem; }

/* Reading support: readable font, wider spacing, shorter lines, read-aloud with word highlighting. */
html[data-profile="reading"] { --bg: #fbf8ef; --text: #1d1d1d; --muted: #454545; --link: #0a4f99; --panel: #f1ece0; }
html[data-profile="reading"] body { font-family: Verdana, Tahoma, "Segoe UI", sans-serif; font-size: 1.15rem;
  line-height: 1.9; letter-spacing: 0.035em; word-spacing: 0.16em; }
html[data-profile="reading"] main, html[data-profile="reading"] footer { max-width: 36rem; }
html[data-profile="reading"] main p, html[data-profile="reading"] main li { margin: 0 0 1.4em; }
@media (min-width: 48rem) and (min-height: 36rem) {  /* on a phone the bar would cover the text */
  html[data-profile="reading"] { scroll-padding-top: 12rem; }
  html[data-profile="reading"] .profile-bar { position: sticky; top: 0; z-index: 5; }
}
html[data-profile="reading"] .ra-bar { display: flex; }
html[data-profile="reading"] .ra-bar[hidden] { display: none; }
html[data-profile="reading"] .ra-current { background: #fff0b3; box-shadow: 0 0 0 0.3em #fff0b3; }
html[data-profile="reading"] figure.ra-current { outline: 4px solid #8a6100; outline-offset: 4px; }
::highlight(ra-word) { background-color: #1d3f8f; color: #ffffff; }

/* Keyboard: large focus outlines and targets. */
html[data-profile="keyboard"] :focus-visible { outline: 4px solid #0b5cad; outline-offset: 3px;
  box-shadow: 0 0 0 7px #ffd54f; }
html[data-profile="keyboard"] button, html[data-profile="keyboard"] select,
html[data-profile="keyboard"] .skip-link { min-height: 48px; min-width: 48px; padding: 0.6rem 1rem; font-size: 1.05rem; }
html[data-profile="keyboard"] .profile-bar label { min-height: 48px; padding: 0 0.5rem; }
html[data-profile="keyboard"] .profile-bar input[type=radio] { width: 1.4rem; height: 1.4rem; }
html[data-profile="keyboard"] main a, html[data-profile="keyboard"] footer a { padding: 0.2em 0.1em; }
html[data-profile="keyboard"] .review-summary li a { display: inline-block; padding: 0.6rem 0.25rem; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>try {{ var p = localStorage.getItem("reading-profile");
  if (["low-vision", "reading", "keyboard"].indexOf(p) >= 0) document.documentElement.dataset.profile = p; }} catch (e) {{}}</script>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<header class="profile-bar" hidden>
<fieldset>
<legend>Reading profile</legend>
<label><input type="radio" name="profile" value="screen-reader"> Screen reader</label>
<label><input type="radio" name="profile" value="low-vision"> Low vision</label>
<label><input type="radio" name="profile" value="reading"> Reading support</label>
<label><input type="radio" name="profile" value="keyboard"> Keyboard</label>
</fieldset>
<p class="profile-hint">Changes how the page looks and works. The content is the same in every profile.</p>
<div class="ra-bar" role="group" aria-labelledby="ra-label">
<span class="ra-label" id="ra-label">Read aloud</span>
<button type="button" class="ra-play">Play</button>
<button type="button" class="ra-prev">Previous</button>
<button type="button" class="ra-next">Next</button>
<button type="button" class="ra-stop">Stop</button>
<label>Speed <select class="ra-rate"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option></select></label>
<p class="ra-status" role="status"></p>
</div>
</header>
<main id="main" tabindex="-1">
<h1>{h1}</h1>
<p>{intro}</p>
<p class="blindfold-launch" hidden><button type="button" id="blindfold-open">Blindfold mode: hear this page as a screen reader reads it</button></p>
{review}
{body}
</main>
<footer><p>Source: {source}</p></footer>
<script type="application/json" id="blindfold-original">{blindfold_data}</script>
<script>{sentences_js}</script>
<script>{blindfold_js}</script>
<script>{profiles_js}</script>
<script>{sonify_js}</script>
</body>
</html>
"""
