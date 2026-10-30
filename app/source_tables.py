"""Source-table values: when a printed table on the chart's page or the next/previous page holds the
values the chart draws, use the table's exact values instead of readings off the axis.

Code only, no model calls. The chart is read independently first (extract_values); a table is used
only when every reading matches one of its columns (or rows) by label, and by series for several
series, within the verifier's tolerance (tools.value_tol). One unmatched point, or two tables that
fit equally well, and the readings stay, marked approximate. The table's values then replace the
readings in the data table, and the description gains one sentence quoting them as printed, with
the source ("Values from Table 2.9: ..."). Never invents a value: every number comes from the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent import tools
from app.models import DataPoint, Description, ExtractedValues, FigureResult, Region, SourceTable

TABLE_LABEL = re.compile(r"^\s*Table\s+\d+(?:\.\d+)*", re.I)
DASHES = re.compile(r"[‐-―−�-]")
CELL = re.compile(r"^\(?([-−–]?)\$?(\d[\d,]*(?:\.\d+)?|\.\d+)\s*%?\)?$")


def norm(label: str) -> str:
    """'10–19' and '10-19', '50 +' and '50+', case and spacing all compare equal."""
    s = DASHES.sub("-", (label or "").strip().lower())
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s*\+", "+", s)
    return re.sub(r"\s+", " ", s)


def cell_value(cell: str | None) -> float | None:
    m = CELL.match((cell or "").strip())
    if not m:
        return None
    v = float(m.group(2).replace(",", ""))
    return -v if m.group(1) else v


@dataclass
class Vector:
    """One column (or row) of a table: label → (value, printed cell, printed label)."""
    name: str
    values: dict[str, tuple[float, str, str]]
    label_head: str = ""  # the label column's header ("Year"), to say "Year 3" rather than "3"


@dataclass
class Table:
    caption: str
    pages: list[int]
    rows: list[list[str]]
    vectors: list[Vector] = field(default_factory=list)


def _groups(tables: list[Region]) -> list[Table]:
    """Table regions, with a table continued on the next page (same 'Table N' caption) joined."""
    out: list[Table] = []
    for r in sorted(tables, key=lambda r: (r.page, r.bbox.y0)):
        if not r.table_rows or r.stem_leaf:
            continue
        rows = [[(c or "").strip() for c in row] for row in r.table_rows]
        m = TABLE_LABEL.match(r.caption or "")
        label = m.group(0).strip() if m else None
        prev = next((t for t in reversed(out) if label and t.caption == label and r.page - t.pages[-1] in (0, 1)), None)
        if prev:
            head = prev.rows[:2]
            prev.rows += [row for row in rows if row not in head]
            if r.page not in prev.pages:
                prev.pages.append(r.page)
        else:
            out.append(Table(caption=label or f"the table on PDF page {r.page}", pages=[r.page], rows=rows))
    for t in out:
        t.vectors = _vectors(t.rows)
    return out


def _vectors(rows: list[list[str]]) -> list[Vector]:
    """Every numeric column (labels in the first column) and every numeric row (labels in the
    header row): a chart may draw either."""
    h = next((i for i, row in enumerate(rows) if sum(bool(c) for c in row) >= 2), None)
    if h is None:
        return []
    head, body = rows[h], rows[h + 1:]
    out = []
    for j in range(1, len(head)):
        vals = {norm(r[0]): (cell_value(r[j]), r[j], r[0]) for r in body if len(r) > j and r[0] and cell_value(r[j]) is not None}
        if len(vals) >= 2:
            out.append(Vector(head[j], vals, head[0]))
    for r in body:
        vals = {norm(head[j]): (cell_value(r[j]), r[j], head[j]) for j in range(1, min(len(r), len(head)))
                if head[j] and cell_value(r[j]) is not None}
        if r and r[0] and len(vals) >= 2:
            out.append(Vector(r[0], vals))
    return out


def _lookup(vec: Vector, label: str, x_label: str | None) -> tuple[float, str, str] | None:
    k = norm(label)
    if k in vec.values:
        return vec.values[k]
    x = norm(x_label or "")
    if x and k.startswith(x + " "):  # "Year 3" on the chart, "3" in the table's Year column
        return vec.values.get(k[len(x) + 1:])
    return None


def _fits(vec: Vector, pts: list[DataPoint], ex: ExtractedValues, tol: float) -> bool:
    for p in pts:
        hit = _lookup(vec, p.label, ex.x_axis_label)
        if hit is None or abs(hit[0] - p.value) > tol:
            return False
    return True


@dataclass
class Match:
    table: Table
    by_series: dict[str | None, Vector]  # chart series → the table vector it matched


def usable(res: FigureResult) -> bool:
    """Charts whose readings can be matched: numeric scale, every point valued, one per label per
    series, series told apart by more than colour, no box plots."""
    ex = res.extracted
    c = res.classification
    if not (ex and res.description and c and c.type.value == "chart" and not c.is_decorative):
        return False
    pts = ex.data
    if len(pts) < 2 or any(p.value is None or not p.label for p in pts) or ex.boxes or ex.source:
        return False
    if tools.no_numeric_scale(ex):  # readings are guesses: matching them would prove nothing
        return False
    if ex.series_identified_by == "color_only" and tools.series_count(ex) >= 2:
        return False
    return len({(norm(p.label), p.series) for p in pts}) == len(pts)


def match(res: FigureResult, tables: list[Region]) -> tuple[Match | None, str | None]:
    """The one nearby table whose values every reading matches. Returns (match, why not) — the
    reason only when a nearby table had the chart's labels but not its values, or fit ambiguously."""
    if not usable(res):
        return None, None
    ex, page = res.extracted, res.region.page
    near = [t for t in _groups(tables) if any(abs(p - page) <= 1 for p in t.pages)]
    tol = tools.value_tol(ex)
    series: dict[str | None, list[DataPoint]] = {}
    for p in ex.data:
        series.setdefault(p.series, []).append(p)
    context = tools._words(" ".join([ex.title or "", ex.y_axis_label or ""]))

    found: list[Match] = []
    why = None
    for t in near:
        by_series: dict[str | None, Vector] = {}
        for name, pts in series.items():
            fits = [v for v in t.vectors if _fits(v, pts, ex, tol)]
            if len(fits) > 1:  # prefer the column named like the series (or the chart's title/axis)
                words = tools._words(name) if name else context
                named = [v for v in fits if tools._words(v.name) & words]
                fits = named if named else fits
            fits = [v for v in fits if v not in by_series.values()]
            if len(fits) != 1:
                if len(fits) > 1:
                    why = f"{t.caption} fits in {len(fits)} columns equally well"
                elif any(all(_lookup(v, p.label, ex.x_axis_label) for p in pts) for v in t.vectors):
                    why = why or f"{t.caption} has the chart's labels, but not every value is within ±{tol:g}"
                break
            by_series[name] = fits[0]
        else:
            found.append(Match(t, by_series))
    if len(found) > 1:
        return None, f"{len(found)} nearby tables fit ({', '.join(m.table.caption for m in found)})"
    return (found[0], None) if found else (None, why)


def _sentence(m: Match, ex: ExtractedValues) -> str:
    def items(name, vec):
        pts = [p for p in ex.data if p.series == name]
        def lab(printed):
            return f"{vec.label_head} {printed}" if vec.label_head and re.fullmatch(r"[\d.]+", printed) else printed
        return "; ".join(f"{lab(hit[2])}, {hit[1]}" for hit in (_lookup(vec, p.label, ex.x_axis_label) for p in pts))
    if len(m.by_series) == 1:
        (name, vec), = m.by_series.items()
        return f"Values from {m.table.caption}: {items(name, vec)}."
    return f"Values from {m.table.caption}: " + " ".join(f"{name or vec.name}: {items(name, vec)}."
                                                         for name, vec in m.by_series.items())


def apply(res: FigureResult, m: Match) -> tuple[bool, str]:
    """Put the table's values into the result if the code checks accept them (no new issue).
    Returns (applied, trace summary)."""
    ex, d = res.extracted, res.description
    page_text = f"{res.region.caption or ''} {res.region.nearby_text}"
    readings = [p.model_copy() for p in ex.data]
    exact = [DataPoint(label=p.label, series=p.series,
                       value=_lookup(m.by_series[p.series], p.label, ex.x_axis_label)[0]) for p in ex.data]
    text = " ".join(c for row in m.table.rows for c in row if c)
    new_ex = ex.model_copy(update={"data": exact, "source": SourceTable(
        caption=m.table.caption, pages=m.table.pages, text=text, readings=readings)})
    sentence = _sentence(m, new_ex)
    new_d = d.model_copy(update={"long_description": f"{d.long_description.rstrip()} {sentence}".strip(),
                                 "data_table": [p.model_copy() for p in exact]})
    before = {(i.kind, i.detail) for i in tools.code_checks(d, ex, page_text)}
    new = [i for i in tools.code_checks(new_d, new_ex, page_text) if (i.kind, i.detail) not in before]
    pages = (f"PDF pages {m.table.pages[0]}–{m.table.pages[-1]}" if len(m.table.pages) > 1
             else f"PDF page {m.table.pages[0]}")
    if new:
        return False, (f"Every reading matches {m.table.caption} ({pages}), but its values raise "
                       f"{'; '.join(i.detail for i in new)[:200]} → keeping the readings (approximate)")
    res.extracted, res.description = new_ex, new_d
    changes = ", ".join(f"{p.label}{f' ({p.series})' if p.series else ''} {e.value:g}"
                        + (f" (read {p.value:g})" if p.value != e.value else "") for p, e in zip(readings, exact))
    return True, (f"Every reading matches {m.table.caption} ({pages}) within ±{tools.value_tol(ex):g} → "
                  f"using the table's exact values: {changes}")
