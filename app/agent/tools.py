"""Agent tools (M3 + M4): classify, extract_values, describe, verify.

Design rule: describe() and extract_values() never see each other's output. The verifier
compares two independent readings plus the image; if describe used the extracted values,
their errors would agree and the check would be circular.

Every tool:
  - takes a Region (and earlier results), sends the cropped image + prompt to Muse Spark
  - validates the reply against a Pydantic model; on invalid JSON it retries once with the error
  - caches through app.llm.chat, so re-runs cost nothing
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import chat
from app.models import (Classification, DataPoint, Description, ExtractedValues, Issue, Region,
                        Verification)

PROMPTS = Path(__file__).parent / "prompts"
T = TypeVar("T", bound=BaseModel)

ALT_MAX = 150
PASS_THRESHOLD = 0.8  # verified descriptions below this go to a human


def _prompt(name: str, **fields) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in fields.items():
        text = text.replace("{" + k + "}", str(v) if v is not None else "none")
    return text


def _image_part(path: str) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def _ask(model: type[T], prompt: str, image: str, step: str, backend: str | None, **kw) -> T:
    """kw: sampling options for chat(), e.g. temperature."""
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(image)]}]
    text = chat(messages, backend=backend, step=step, **kw)
    try:
        return model.model_validate(_json(text))
    except (ValueError, ValidationError) as e:
        # One repair attempt, telling the model what was wrong.
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not valid JSON for the schema ({e}). "
                                                 "Reply again with ONLY the corrected JSON."}]
        text = chat(messages, backend=backend, step=step + "_repair", **kw)
        return model.model_validate(_json(text))


# ---------- tools ----------

def classify_region(region: Region, backend: str | None = None) -> Classification:
    p = _prompt("classify", caption=region.caption, nearby_text=region.nearby_text)
    return _ask(Classification, p, region.crop_path, "classify", backend)


def extract_values(region: Region, kind: str, backend: str | None = None) -> ExtractedValues:
    p = _prompt("extract_values", type=kind)
    ex = _ask(ExtractedValues, p, region.crop_path, "extract_values", backend, temperature=0)
    if ex.boxes:  # box numbers become data points (label = statistic, series = box), checked like any value
        pts = [DataPoint(label=stat, value=getattr(b, stat), series=b.name or f"box {i + 1}")
               for i, b in enumerate(ex.boxes) for stat in BOX_STATS if getattr(b, stat) is not None]
        ex.data = [d for d in ex.data if box_stat(d.label) is None] + pts
    return ex


def describe(region: Region, kind: str, backend: str | None = None,
             feedback: str | None = None, no_scale: bool = False) -> Description:
    """feedback: issues from a failed verification, used when the agent rewrites.
    no_scale: the value axis has no numbers (see no_numeric_scale)."""
    p = _prompt("describe", type=kind, caption=region.caption, nearby_text=region.nearby_text)
    if no_scale:
        p += "\n\n" + _prompt("describe_no_scale")
    if feedback:
        p += ("\n\nA fact-checker found these problems in your previous description. "
              "Fix them, re-reading the figure carefully:\n" + feedback)
    # Rewrites must not come from cache, or the agent would get the same answer back.
    step = "describe" if not feedback else "describe_retry:" + hashlib.md5(feedback.encode()).hexdigest()[:8]
    return _ask(Description, p, region.crop_path, step, backend)


def verify(region: Region, desc: Description, extracted: ExtractedValues,
           backend: str | None = None) -> Verification:
    p = _prompt("verify",
                extracted=extracted.model_dump_json(indent=1),
                alt_text=desc.alt_text,
                long_description=desc.long_description,
                data_table=json.dumps([d.model_dump() for d in desc.data_table]))
    result = _ask(Verification, p, region.crop_path, "verify", backend, temperature=0)
    for issue in result.issues:
        issue.source = "model"

    code_issues = code_checks(desc, extracted, f"{region.caption or ''} {region.nearby_text}")
    if code_issues:
        result.issues += code_issues
        result.passed = False
        result.confidence = min(result.confidence, 0.6)
    if result.confidence < PASS_THRESHOLD:
        result.passed = False
    return result


class Reread(BaseModel):
    value: float
    printed: bool = False
    confidence: float = 0.5
    how: str = ""


def reread_value(region: Region, label: str, a, b, series: str | None = None,
                 backend: str | None = None, zoom_path: str | None = None) -> Reread:
    """Re-read one data point from an enlarged crop (zoom_path) when two readings disagree."""
    p = _prompt("reread", label=label, a=a, b=b,
                series=f' (series "{series}")' if series else "")
    return _ask(Reread, p, zoom_path or region.crop_path, f"reread:{label}", backend)


# ---------- deterministic checks (free, reliable, explainable) ----------

_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*")


def _numbers(text: str) -> list[float]:
    out = []
    # Book text writes negatives with an en dash or minus sign ("–2.4"); ranges ("10–20") stay
    # positive because _NUM needs no word character before the sign.
    for m in _NUM.findall(text.replace("–", "-").replace("−", "-")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


# Box-plot statistics, by name first, then by the drawn part a description may name instead
# ("left whisker end", "right edge of box", "dashed line"). Box parts before whisker ends.
BOX_STATS = ("min", "q1", "median", "q3", "max")
_STAT_NAMES = [("min", r"\b(min|minimum|lowest|smallest)\b"), ("q1", r"\b(q1|first quartile|lower quartile|25th)\b"),
               ("median", r"\b(median|q2)\b"), ("q3", r"\b(q3|third quartile|upper quartile|75th)\b"),
               ("max", r"\b(max|maximum|highest|largest)\b")]
_STAT_PARTS = [("median", r"\b(middle line|dashed line|line inside)\b"),
               ("q1", r"\b(left|lower|bottom)\b.*\b(edge|side)\b|\bbox\b.*\b(left|lower|bottom|start)\b"),
               ("q3", r"\b(right|upper|top)\b.*\b(edge|side)\b|\bbox\b.*\b(right|upper|top|end)\b"),
               ("min", r"\b(left|lower|bottom)\b.*\b(whisker|end|tip)\b|\bwhisker\b.*\b(left|lower|bottom)\b"),
               ("max", r"\b(right|upper|top)\b.*\b(whisker|end|tip)\b|\bwhisker\b.*\b(right|upper|top)\b")]


def box_stat(label: str) -> tuple[str, str] | None:
    """(statistic, rest of the label) for a box-plot data label, else None."""
    low = label.lower()
    for stat, pat in _STAT_NAMES + _STAT_PARTS:
        if re.search(pat, low):
            rest = [w for w in re.sub(pat, " ", low).split() if w not in _PART_WORDS]
            return stat, " ".join(rest).strip(" -:,()")
    return None


_PART_WORDS = {"of", "the", "box", "edge", "side", "whisker", "end", "tip", "line", "inside", "dashed",
               "left", "right", "upper", "lower", "top", "bottom", "-", ":"}


def _box_rows(desc: Description, ex: ExtractedValues) -> Description:
    """The description's data table with box-plot labels normalized to the extracted points
    (label = statistic; series from the row, else the rest of the label, e.g. 'China max')."""
    rows = []
    for d in desc.data_table:
        s = box_stat(d.label)
        rows.append(DataPoint(label=s[0], value=d.value, series=d.series or s[1] or None) if s else d)
    return desc.model_copy(update={"data_table": rows})


_NO_MEDIAN = re.compile(r"\bno\b[^.]{0,60}\bmedian\b|\bmedian\b[^.]{0,40}\bnot\b[^.]{0,20}\b(visible|drawn|shown)\b",
                        re.IGNORECASE)


def _median_checks(desc: Description, ex: ExtractedValues) -> list[Issue]:
    """Whether a median line is drawn must match: no median given for a box without one,
    no 'no median line' for a box that has one."""
    issues = []
    drawn = [b for b in ex.boxes if b.median_drawn and b.median is not None]
    given = [d for d in desc.data_table if (box_stat(d.label) or ("",))[0] == "median" and d.value is not None]
    if len(given) > len(drawn):
        issues.append(Issue(kind="wrong_number", source="code",
                            detail=f"The data table gives {len(given)} median(s), but only {len(drawn)} "
                                   "box(es) have a median line drawn."))
    for sent in re.split(r"(?<=[.;])\s+", desc.long_description):
        if not _NO_MEDIAN.search(sent):
            continue
        named = [b for b in ex.boxes if b.name and _words(b.name) and _words(b.name) <= _words(sent)]
        if any(b in drawn for b in named) or (not named and len(drawn) == len(ex.boxes) > 0):
            issues.append(Issue(kind="invented_detail", source="code",
                                detail=f"The description says no median line is drawn ('{sent.strip()[:80]}'), "
                                       "but the independent reading has one."))
    return issues


def no_numeric_scale(ex: ExtractedValues) -> bool:
    """Values were read off an axis that has no numbers: any exact value is a guess
    (e.g. bar heights against unlabeled gridlines)."""
    scale = {"x": ex.x_axis_numeric_scale, "y": ex.y_axis_numeric_scale}.get(ex.value_axis or "")
    return bool(ex.values_estimated and scale is False and any(d.value is not None for d in ex.data))


def code_checks(desc: Description, ex: ExtractedValues, page_text: str = "") -> list[Issue]:
    issues: list[Issue] = []

    # Rewrite feedback must not leak into what the student reads ("This text is explicitly
    # visible in the image and is not invented.").
    leaked = sorted({m.group(0).lower() for m in LEAK_RE.finditer(f"{desc.alt_text} {desc.long_description}")})
    if leaked:
        issues.append(Issue(kind="leaked_feedback", source="code",
                            detail=f"The description talks about the checking process ({', '.join(leaked)}); "
                                   "remove that and describe only the figure."))

    if len(desc.alt_text) > ALT_MAX:
        issues.append(Issue(kind="alt_text_problem", source="code",
                            detail=f"Alt text is {len(desc.alt_text)} characters (max {ALT_MAX})."))

    # Box plots: statistics compared box by box like any value; the median line must match too.
    if ex.boxes:
        issues += _median_checks(desc, ex)
        desc = _box_rows(desc, ex)
    box_pts = [d for d in ex.data if d.label in BOX_STATS and d.value is not None] if ex.boxes else []

    if no_numeric_scale(ex):
        # Both readings are guesses, so comparing them proves nothing. Every number must come from
        # the page text, the figure's own labels (e.g. the other axis), or be a count of its parts.
        # Boxes drawn over such a chart sit on the other axis, which has numbers: checked as usual.
        allowed = _numbers(" ".join([page_text, ex.title or ""] + ex.labels))
        allowed += [len(ex.data) - len(box_pts), series_count(ex), len(ex.boxes)]
        btol = 0.0
        if box_pts:
            bv = [d.value for d in box_pts]
            btol = max(0.04 * (max(bv) - min(bv)), 0.01 * max(abs(v) for v in bv), 1e-9)
            issues += _compare_values(desc, ex.model_copy(update={"data": box_pts}), btol)
        said = [d.value for d in desc.data_table if d.value is not None and d.label not in BOX_STATS] + _numbers(
            f"{desc.alt_text} {desc.long_description}")
        unsupported = sorted({n for n in said if not any(abs(n - a) <= 1e-6 * max(abs(a), 1) for a in allowed)
                              and not any(abs(n - d.value) <= btol for d in box_pts)})
        if unsupported:
            issues.append(Issue(kind="no_numeric_scale", source="code",
                                detail="The value axis has no numbers, but the description gives values "
                                       f"not in the page text: {', '.join(f'{n:g}' for n in unsupported)}."))
        return issues + _label_checks(desc, ex)

    # Data table vs independently extracted values, matched by label (and series, see below).
    vals = [d.value for d in ex.data if d.value is not None]
    span = (max(vals) - min(vals)) if len(vals) > 1 else max([abs(v) for v in vals] or [1])
    biggest = max([abs(v) for v in vals] or [1])
    # Values read off an axis carry reading error; printed values should match almost exactly.
    # (Tuned on Figure 2.10: a true 201.6 vs an axis reading of 200 must not fail;
    #  a planted 232 vs 207 must.)
    tol = max(0.04 * span, 0.01 * biggest) if ex.values_estimated else 0.005 * max(span, 1)
    issues += _compare_values(desc, ex, max(tol, 1e-9))

    # Numbers in the text that appear nowhere in the figure (possible invented values).
    known = set(_numbers(" ".join(ex.labels + [ex.title or "", ex.trend or ""])))
    known |= {d.value for d in ex.data if d.value is not None}
    if known:
        for n in set(_numbers(desc.long_description)):
            close = any(abs(n - k) <= max(tol, 0.01 * max(abs(k), 1)) for k in known)
            if not close and not (1 <= n <= 12 and n == int(n)):  # ignore small counts ("3 bars")
                issues.append(Issue(kind="invented_detail", source="code",
                                    detail=f"The number {n:g} is in the description but not in the figure."))

    return issues + _label_checks(desc, ex)


def _label_checks(desc: Description, ex: ExtractedValues) -> list[Issue]:
    # Title and axis labels must be mentioned. Loose match: most of the label's words appear,
    # so "Annual CPI" wording differences don't fail but a dropped axis title does.
    issues: list[Issue] = []
    have = _words(desc.long_description)
    for what, label in (("title", ex.title), ("x-axis label", ex.x_axis_label),
                        ("y-axis label", ex.y_axis_label)):
        words = _words(label or "")
        if words and len(words & have) / len(words) < LABEL_MATCH:
            issues.append(Issue(kind="missing_label", source="code",
                                detail=f"The {what} '{label}' is not mentioned in the long description."))
    return issues


def _key(label: str) -> str:
    return label.strip().lower()


def _series_map(desc_pts, ref_pts, tol: float | None = None) -> dict:
    """Map the description's series names onto the independent reading's.

    The two readings name series independently ("light blue" vs "light cyan-blue", or legend
    text vs a colour when there is no legend), so names can't be compared directly. Choose the
    one-to-one mapping under which the most values agree (within tol), then the smallest total
    error, then matching names. Counting agreements rather than summing errors keeps one badly
    wrong value (or two series swapped at one label) from flipping the whole mapping."""
    d_names = list(dict.fromkeys(d.series for d in desc_pts))
    r_names = list(dict.fromkeys(r.series for r in ref_pts))
    ref = {(_key(r.label), r.series): r.value for r in ref_pts}
    labels = {_key(r.label) for r in ref_pts}
    if tol is None:
        vals = [r.value for r in ref_pts if r.value is not None] or [0.0]
        tol = max(0.04 * (max(vals) - min(vals)), 1e-9)

    def cost(perm) -> tuple[int, float, int]:
        agree, err, name_misses = 0, 0.0, 0
        for dn, rn in zip(d_names, perm):
            for d in desc_pts:
                k = (_key(d.label), rn)
                if d.series == dn and d.value is not None and k in ref:
                    err += abs(d.value - ref[k])
                    agree += abs(d.value - ref[k]) <= tol
            name_misses += _key(dn or "") != _key(rn or "")
        return -agree, err, name_misses

    if len(d_names) > len(r_names) or len(r_names) > 6 or not labels:
        return {n: n for n in d_names}  # can't map sensibly: fall back to matching names
    best = min(itertools.permutations(r_names, len(d_names)), key=cost)
    return dict(zip(d_names, best))


def series_count(ex: ExtractedValues) -> int:
    """Number of data series: distinct names, or the most points at one label when unnamed."""
    per_label: dict[str, int] = defaultdict(int)
    for d in ex.data:
        per_label[_key(d.label)] += 1
    return max(len({d.series for d in ex.data}), max(per_label.values(), default=0))


def _compare_values(desc: Description, ex: ExtractedValues, tol: float) -> list[Issue]:
    ref_pts = [d for d in ex.data if d.value is not None]
    desc_pts = [d for d in desc.data_table if d.value is not None]
    issues = []
    if series_count(ex) < 2:
        ref = {_key(d.label): d.value for d in ref_pts}
        for row in desc_pts:
            k = _key(row.label)
            if k in ref and abs(row.value - ref[k]) > tol:
                issues.append(Issue(kind="wrong_number", source="code",
                                    detail=f"'{row.label}': description says {row.value:g}, "
                                           f"independent reading says {ref[k]:g}."))
        return issues

    # Several values per label but no series names in the independent reading: which value belongs
    # to which series can't be checked here, so compare each label's values as a set, paired so
    # that the most values agree (one wrong value must not make its correct neighbour look wrong).
    names_usable = len({(_key(d.label), d.series) for d in ref_pts}) == len(ref_pts)
    if not names_usable:
        by_label: dict[str, list[float]] = defaultdict(list)
        for d in ref_pts:
            by_label[_key(d.label)].append(d.value)
        mine: dict[str, list] = defaultdict(list)
        for d in desc_pts:
            mine[_key(d.label)].append(d)
        for k, rows in mine.items():
            if k not in by_label:
                continue
            ref_vals = by_label[k]
            if len(rows) == len(ref_vals) <= 6:
                pairs = list(zip(rows, min(
                    itertools.permutations(ref_vals),
                    key=lambda perm: (-sum(abs(d.value - v) <= tol for d, v in zip(rows, perm)),
                                      sum(abs(d.value - v) for d, v in zip(rows, perm))))))
            else:  # different counts: each value must be near one of the readings
                pairs = [(d, min(ref_vals, key=lambda v: abs(v - d.value))) for d in rows]
            for d, v in pairs:
                if abs(d.value - v) > tol:
                    issues.append(Issue(kind="wrong_number", source="code",
                                        detail=f"'{d.label}'{f' ({d.series})' if d.series else ''}: description "
                                               f"says {d.value:g}, but the independent reading has "
                                               f"{', '.join(f'{x:g}' for x in by_label[k])} at this label."))
        return issues

    # Several series share each label (overlaid lines, grouped bars): compare series to series.
    ref = {(_key(d.label), d.series): d.value for d in ref_pts}
    named = [d for d in desc_pts if d.series is not None]
    mapping = _series_map(named, ref_pts, tol) if named else {}
    for row in desc_pts:
        k = _key(row.label)
        if row.series is None:
            # No series given: it must match one of the values at this label.
            options = [v for (lab, _), v in ref.items() if lab == k]
            if options and min(abs(row.value - v) for v in options) > tol:
                issues.append(Issue(kind="wrong_number", source="code",
                                    detail=f"'{row.label}': description says {row.value:g}, but the "
                                           f"independent reading has {', '.join(f'{v:g}' for v in options)}."))
            continue
        rs = mapping.get(row.series)
        if (k, rs) in ref and abs(row.value - ref[(k, rs)]) > tol:
            issues.append(Issue(kind="wrong_number", source="code",
                                detail=f"'{row.label}' ({row.series}): description says {row.value:g}, "
                                       f"independent reading says {ref[(k, rs)]:g} ({rs})."))
    return issues


LEAK_RE = re.compile(r"\b(verifier|fact[- ]?checker|previous description|earlier description|"
                     r"feedback|issues?|not invented)\b", re.IGNORECASE)
LABEL_MATCH = 0.6  # share of a label's words that must appear in the description
_STOP = {"a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "by", "per", "vs"}


def _words(text: str) -> set[str]:
    """Lowercase content words, with a plural 's' stripped ("Years" matches "year")."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w not in _STOP:
            out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out
