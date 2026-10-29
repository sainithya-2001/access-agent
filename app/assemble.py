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
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz

from app import stemplot
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
    out = [f'<table class="data-table" id="{fid}-data">',
           f"<caption>Data for {e(caption)}.{e(note)}</caption>",
           "<thead><tr>" + f'<th scope="col">{first}</th>' + "".join(f'<th scope="col">{h}</th>' for h in heads)
           + "</tr></thead><tbody>"]
    for lab in labels:
        cells = "".join(f"<td>{_num(value[(lab, s)]) if (lab, s) in value else ''}</td>" for s in series)
        out.append(f'<tr><th scope="row">{e(lab)}</th>{cells}</tr>')
    out.append("</tbody></table>")
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

    if has_long:
        paras = "".join(f"<p>{e(_clean(p))}</p>" for p in desc.long_description.split("\n\n") if p.strip())
        parts.append(f'<div class="long-desc" id="{fid}-desc"><p class="long-desc-label">'
                     f"Description of {e(name)}:</p>{paras}</div>")
    if res and kind == "chart" and not decorative:
        parts.append(_chart_table(res, fid, name))
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
    out = [f'<table id="tbl-{r.id}"><caption>{e(caption)}</caption>']
    if header:
        out.append("<thead><tr>" + "".join(f'<th scope="col">{e(c)}</th>' for c in header) + "</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) == 1 and ncols > 1 and len(row) != ncols or (len(row) == 1 and ncols > 1):
            out.append(f'<tr><td colspan="{ncols}">{e(filled[0])}</td></tr>')
        else:
            out.append("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row + [""] * (ncols - len(row))) + "</tr>")
    out.append("</tbody></table>")

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
    span = _ranges([int(x) for x in shown]) if shown else _ranges([p.number for p in doc.pages])
    review_html = ""
    if review:
        lis = "".join(f'<li><a href="#{fid}">{e(text)}</a></li>' for fid, text in review)
        review_html = (f'<section class="review-summary" aria-labelledby="review-heading">'
                       f'<h2 id="review-heading">Staff review: {len(review)} item{"s" if len(review) != 1 else ""} '
                       f"to check</h2><ul>{lis}</ul></section>")
    return PAGE.format(
        title=e(f"{chapter} (pages {span}) | {book}"),
        h1=e(chapter),
        intro=e(f"Accessible version of pages {span} of {book}."),
        review=review_html,
        body="\n".join(out),
        source=e(f"{book}, OpenStax, licensed under CC BY 4.0. PDF pages {first}–{last}."),
        css=CSS,
    )


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
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<main id="main" tabindex="-1">
<h1>{h1}</h1>
<p>{intro}</p>
{review}
{body}
</main>
<footer><p>Source: {source}</p></footer>
</body>
</html>
"""
