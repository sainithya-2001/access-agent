"""Stem-and-leaf plots: expand a stemplot table into its exact data values, in code.

OpenStax prints stemplots as tables (Stem | Leaf). Reading them is deterministic, so no model
is involved and the values can go straight into the HTML export as a data list.

A table counts as a stemplot when its caption or header says stem/leaf, or it has a key row
("Key: 8|0 means 80."). Layouts handled:
  Stem | Leaf                     one series
  Leaf | Stem | Leaf              back-to-back, two series
Each leaf is one digit; value = (stem * 10 + leaf) * leaf_unit. leaf_unit comes from the key;
without one, from the page text (the unit whose values appear there, e.g. 1|1 = 1.1 when the
example lists "1.1; 1.5; ..."); otherwise 1 (4|6 means 46), marked as assumed.
"""

from __future__ import annotations

import re

from app.models import StemLeaf

KEY_RE = re.compile(r"Key:?\s*(\d+)\s*\|\s*(\d+)\s*means\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
STEM_WORD_RE = re.compile(r"\b(stem|leaf|leaves)\b|stem-and-", re.IGNORECASE)


def _text(rows) -> str:
    return " ".join(c or "" for row in rows for c in row)


UNITS = (1.0, 0.1, 0.01, 10.0, 100.0)
_NUM_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")


def _unit_from_text(pairs: list[tuple[int, int]], context: str) -> float | None:
    """The leaf unit under which most of the stemplot's values are printed in the text."""
    nums = {round(float(n), 6) for n in _NUM_RE.findall(context)}
    best, best_hits = None, 0
    for u in UNITS:
        hits = sum(1 for s, d in pairs if round((s * 10 + d) * u, 6) in nums)
        if hits > best_hits:
            best, best_hits = u, hits
    return best if pairs and best_hits >= 0.6 * len(pairs) else None


def parse(rows: list[list[str | None]] | None, caption: str | None = None,
          context: str = "") -> StemLeaf | None:
    """context: page text, used to find the leaf unit when the table has no key."""
    if not rows:
        return None
    width = max(len(r) for r in rows)
    header = next((r for r in rows if len(r) == width), rows[0])
    key = KEY_RE.search(_text(rows))
    if not (STEM_WORD_RE.search(caption or "") or STEM_WORD_RE.search(" ".join(c or "" for c in header)) or key):
        return None

    names = [(c or "").strip() for c in header]
    stem_col = next((i for i, n in enumerate(names) if n.lower() == "stem"), None)
    if stem_col is None:
        stem_col = {2: 0, 3: 1}.get(width)
    if stem_col is None:
        return None
    leaf_cols = [i for i in range(width) if i != stem_col]

    def series_name(i: int) -> str:
        n = names[i]
        if n and not re.fullmatch(r"(?i)leaf|leaves|[\d\s]*", n):
            return n
        return "values" if len(leaf_cols) == 1 else ("left" if i < stem_col else "right")

    pairs: dict[str, list[tuple[int, int]]] = {series_name(i): [] for i in leaf_cols}
    for row in rows:
        if len(row) != width or not re.fullmatch(r"\d+", (row[stem_col] or "").strip()):
            continue  # header, title, key or caption row
        stem = int(row[stem_col].strip())
        for i in leaf_cols:
            pairs[series_name(i)] += [(stem, int(d)) for d in re.findall(r"\d", row[i] or "")]
    every = [p for ps in pairs.values() for p in ps]
    if not every:
        return None

    unit, source = 1.0, "assumed"
    if key:
        stem, leaf, means = int(key.group(1)), int(key.group(2)), float(key.group(3))
        unit, source = (means / (stem * 10 + leaf), "key") if stem * 10 + leaf else (1.0, "assumed")
    elif (found := _unit_from_text(every, context)) is not None:
        unit, source = found, "text"

    series = {k: sorted(round((s * 10 + d) * unit, 10) for s, d in ps) for k, ps in pairs.items()}
    return StemLeaf(series=series, leaf_unit=unit, key=key.group(0) if key else None, unit_source=source)
