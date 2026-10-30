"""Hear the chart: the data a browser needs to play a chart as tones (web/sonify.js).

Offered only for bar charts, histograms, line charts and frequency polygons whose values passed
verification and are approved (by the agent or by staff). Everything else returns None, so the
button never appears for values nobody checked.
"""
from __future__ import annotations

import re

from app.agent import tools
from app.models import FigureResult, RegionType, Status

APPROVED = (Status.auto_approved, Status.approved, Status.edited)

# Most specific first: a frequency polygon is also drawn as a line.
KINDS = [
    ("frequency polygon", re.compile(r"\bfrequency polygons?\b", re.I)),
    ("histogram", re.compile(r"\bhistograms?\b", re.I)),
    ("bar chart", re.compile(r"\bbar (?:chart|graph)s?\b", re.I)),
    ("line chart", re.compile(r"\b(?:line (?:chart|graph|plot)s?|time series)\b", re.I)),
]
# Any of these in the figure means the values may not be one tone per category (box plots store
# their five numbers as data points; a histogram with a box plot above it mixes both).
OTHER = re.compile(r"\b(?:box(?:-and-whisker| and whisker)?[ -]?plots?|boxplots?|dot ?plots?|pie (?:chart|graph)s?"
                   r"|scatter ?(?:plot|diagram)s?|stem-?and-?leaf|stemplots?)\b", re.I)


def chart_kind(res: FigureResult) -> str | None:
    """The chart kind from the classification reason, the alt text and the long description's first
    sentence (verified text; there is no kind field to read)."""
    d, c = res.description, res.classification
    first = re.split(r"(?<=[.!?])\s", d.long_description.strip(), maxsplit=1)[0] if d and d.long_description else ""
    texts = [c.reason if c else "", d.alt_text if d else "", first]
    if any(OTHER.search(t) for t in texts):
        return None
    for kind, rx in KINDS:
        if any(rx.search(t) for t in texts):
            return kind
    return None


def chart_sound(res: FigureResult) -> dict | None:
    c, ex, v = res.classification, res.extracted, res.verification
    if not (c and ex and v and c.type == RegionType.chart and not c.is_decorative):
        return None
    if res.status not in APPROVED or not v.passed:
        return None
    if tools.no_numeric_scale(ex) or ex.series_identified_by == "color_only" or ex.boxes:
        return None
    kind = chart_kind(res)
    if not kind or len(ex.data) < 2 or any(p.value is None or not p.label for p in ex.data):
        return None
    series: dict[str | None, list[dict]] = {}
    for p in ex.data:
        series.setdefault(p.series, []).append({"label": p.label, "value": p.value})
    for points in series.values():  # one tone per category: repeated labels in a series are not a chart line
        if len({tools._key(p["label"]) for p in points}) != len(points):
            return None
    values = [p.value for p in ex.data]
    single = len(series) == 1
    return {
        "kind": kind,
        "title": ex.title or "",
        "x_label": ex.x_axis_label or "",
        "y_label": ex.y_axis_label or "",
        "min": min(values),
        "max": max(values),
        # Bars and histograms grow from zero, so pitch starts at zero too.
        "from_zero": kind in ("bar chart", "histogram") and min(values) >= 0,
        "series": [{"name": (ex.y_axis_label or "Values") if single and not name else (name or "Unnamed series"),
                    "points": points} for name, points in series.items()],
    }
