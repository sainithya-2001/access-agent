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
                extracted=extracted.model_dump_json(indent=1, exclude={"source"}),  # set in code, not read
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
    if ex.source:  # values from a printed table (app/source_tables.py): its numbers are the page's
        page_text = f"{page_text} {ex.source.caption} {ex.source.text}"

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
    tol = value_tol(ex)
    issues += _compare_values(desc, ex, tol)
    issues += _duplicate_rows(desc, ex)
    issues += _count_checks(desc, ex)
    issues += _comparison_checks(desc, ex)

    # Numbers in the text that appear nowhere in the figure (possible invented values). Numbers from
    # the caption and page text are allowed as written ("20-year-olds"); that a text value is given
    # as the text's, not as a drawn position, is the describe prompt's rule.
    known = set(_numbers(" ".join(ex.labels + [ex.title or "", ex.trend or ""])))
    known |= {d.value for d in ex.data if d.value is not None}
    if ex.source:  # the description was written from the drawing: numbers near the readings still hold
        known |= {d.value for d in ex.source.readings if d.value is not None}
    from_text = set(_numbers(page_text))
    if known:
        for n in set(_numbers(desc.long_description)):
            close = any(abs(n - k) <= max(tol, 0.01 * max(abs(k), 1)) for k in known)
            if not close and n not in from_text and not (0 <= n <= 12 and n == int(n)):  # counts: "no X's", "3 bars"
                issues.append(Issue(kind="invented_detail", source="code",
                                    detail=f"The number {n:g} is in the description but not in the figure."))

    return issues + _label_checks(desc, ex)


def value_tol(ex: ExtractedValues) -> float:
    """How far two readings of a value may differ and still agree."""
    vals = [d.value for d in ex.data if d.value is not None]
    span = (max(vals) - min(vals)) if len(vals) > 1 else max([abs(v) for v in vals] or [1])
    biggest = max([abs(v) for v in vals] or [1])
    # Values read off an axis carry reading error; printed values should match almost exactly.
    # (Tuned on Figure 2.10: a true 201.6 vs an axis reading of 200 must not fail;
    #  a planted 232 vs 207 must.)
    tol = max(0.04 * span, 0.01 * biggest) if ex.values_estimated else 0.005 * max(span, 1)
    return max(tol, 1e-9)


def _single_series(ex: ExtractedValues) -> bool:
    return ex.series_identified_by == "single_series" and not ex.boxes


def duplicate_labels(rows, ex: ExtractedValues) -> list[str]:
    """Labels with more than one value in a single-series chart's table (e.g. a dot plot listed one
    row per mark): one value per label is the only table a reader, or a check, can use."""
    if not _single_series(ex):
        return []
    seen: dict[tuple, int] = defaultdict(int)
    for d in rows:
        if d.value is not None:
            seen[(_key(d.label), d.series)] += 1
    return sorted({lab for (lab, _), n in seen.items() if n > 1})


def _duplicate_rows(desc: Description, ex: ExtractedValues) -> list[Issue]:
    dups = duplicate_labels(desc.data_table, ex)
    return [Issue(kind="duplicate_rows", source="code",
                  detail=f"The data table has several rows for {', '.join(repr(d) for d in dups)}; give one value "
                         "per label (for a dot plot, the number of marks at each value).")] if dups else []


# ---------- stated counts ("eight bars", "three box plots") ----------
_NUMWORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMBER = r"\d{1,3}(?:\.\d+)?|" + "|".join(_NUMWORDS)
_COUNT_NOUNS = {"categories": r"bars|categories|bins|intervals|classes|columns|groups|slices|segments",
                "series": r"lines|series|curves|polygons",
                "boxes": r"box ?plots|boxes",
                "marks": r"x['’]?s|dots|marks|observations"}
_GAP_WORD = r"(?!(?:and|or|with|in|to|from|of|at|by|for|the|a|an|is|are|than)\b)[a-z-]+"
_COUNT_RE = re.compile(r"(?<![\d.,–-])\b(\d{1,3}|" + "|".join(_NUMWORDS) + r")\s+((?:" + _GAP_WORD + r"\s+){0,3}?)(?:"
                       + "|".join(f"(?P<{k}>{v})" for k, v in _COUNT_NOUNS.items()) + r")\b", re.I)
# A count with one of these is about part of the set ("the other six", "the two tallest bars",
# "the three bins to the right of the peak"), so it is only checked for being more than the whole.
_SUBSET_BEFORE = re.compile(r"\b(other|remaining|first|last|top|bottom|next|these|those)\s*$", re.I)
_SUBSET_WORDS = re.compile(r"\b(tallest|shortest|highest|lowest|largest|smallest|youngest|oldest|isolated|"
                           r"higher|lower|leftmost|rightmost|outer|inner|middle|central|extreme)\b", re.I)
_SUBSET_AFTER = re.compile(r"^\s*(to the|on the|of the|of these|above|below|before|after|left|right|near|"
                           r"between|at|in the|beyond|past|around|over|on|stacked|piled)\b", re.I)


def _count_checks(desc: Description, ex: ExtractedValues) -> list[Issue]:
    """A count the description states for the whole figure must match the independent reading:
    categories (distinct labels), series, boxes, or marks (the sum of counts, e.g. a dot plot's X's)."""
    pts = [d for d in ex.data if d.value is not None and not (ex.boxes and d.label in BOX_STATS)]
    named = {d.series for d in ex.data if d.series} if not ex.boxes else set()
    expected = {
        "categories": len({_key(d.label) for d in pts}) or None,
        "series": (1 if _single_series(ex) else len(named)) or None,
        "boxes": len(ex.boxes) or None,
        "marks": int(sum(d.value for d in pts)) if pts and _single_series(ex)
        and all(d.value >= 0 and d.value == int(d.value) for d in pts) else None,
    }
    # A count that is part of a name in the figure ("BMW 5 Series") is not a count.
    names = " | ".join(ex.labels + [ex.title or ""] + [b.name or "" for b in ex.boxes]
                       + [d.series or "" for d in ex.data]).lower()
    issues, text = [], f"{desc.alt_text}\n{desc.long_description}"
    for m in _COUNT_RE.finditer(text):
        kind = next(k for k in _COUNT_NOUNS if m.group(k))
        want = expected[kind]
        if want is None or f"{m.group(1)} {m.group(kind)}".lower() in names:
            continue
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMWORDS[m.group(1).lower()]
        if n <= 1:
            continue
        subset = bool(_SUBSET_BEFORE.search(text[max(0, m.start() - 20):m.start()])
                      or _SUBSET_WORDS.search(m.group(2)) or _SUBSET_AFTER.match(text[m.end():m.end() + 20]))
        if n > want or (n != want and not subset):
            issues.append(Issue(kind="wrong_count", source="code",
                                detail=f"'{m.group(0).strip()}': the independent reading has {want} "
                                       f"{'marks' if kind == 'marks' else kind}."))
    return issues


# ---------- comparisons ("about twice as tall as", "a wider box") ----------
_FRAC = r"(?:a|one)?[- ]?(?:half|third|quarter|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
_RATIO_RE = re.compile(
    r"\b(?:(?P<twice>twice)|(?P<lo>" + _NUMBER + r")\s*(?:to|-|–|or)\s*(?P<hi>" + _NUMBER + r")\s+times"
    r"|(?P<n>" + _NUMBER + r")\s+times|(?P<flo>" + _FRAC + r")(?:\s*(?:to|-|–|or)\s*(?P<fhi>" + _FRAC + r"))?)"
    r"\s+(?:(?:as|of)\s+)?(?:the\s+)?(?:tall|high|large|big|long|height)", re.I)
_FRACS = {"half": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
          "ninth": 9, "tenth": 10}


def _frac(w: str) -> float:
    return 1 / _FRACS[re.search(r"(half|third|quarter|fourth|fifth|sixth|seventh|eighth|ninth|tenth)", w.lower()).group(1)]
RATIO_TOL = 0.15  # a stated ratio may be off by 15% ("about twice" covers 1.74 to 2.3)


def _num(w: str) -> float:
    return float(w) if w[0].isdigit() else float(_NUMWORDS[w.lower()])


def _mentions(sentence: str, pts) -> list[tuple[int, int, float, bool]]:
    """(start, end, value, is_label) for each data label or extreme group ("the shortest bars") the
    sentence names. "The next tallest" is not the tallest, so it names nothing."""
    out = []
    for d in pts:
        lab = d.label.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", lab):
            # A number names a bar only after "at", "bar" etc., possibly in a list: "at 4, 5, 9, and 10".
            pat = (r"\b(?:at|bars?|for|over|above|centered at)\s+(?:-?\d+(?:\.\d+)?(?:,\s*|,?\s+and\s+))*"
                   + re.escape(lab) + r"(?![\d.]*\d)")
        else:
            pat = r"\b" + re.escape(lab) + r"\b"
        for m in re.finditer(pat, sentence, re.I):
            out.append((m.end() - len(lab), m.end(), d.value, True))
    vals = [d.value for d in pts]
    for words, v in ((r"shortest|lowest|smallest", min(vals)), (r"tallest|highest|largest", max(vals))):
        for m in re.finditer(r"(?<!next )(?<!second )(?<!third )\b(" + words + r")\b", sentence, re.I):
            out.append((m.start(), m.end(), v, False))
    return sorted(out)


def _comparison_checks(desc: Description, ex: ExtractedValues) -> list[Issue]:
    """Ratios between named bars, and two-box comparisons, checked against the independent reading.
    Shape words ("bimodal", "skewed") are left to the model check."""
    issues = []
    sentences = re.split(r"(?<=[.;])\s+", f"{desc.alt_text} {desc.long_description}")
    pts = [d for d in ex.data if d.value is not None and d.label not in BOX_STATS]
    if pts and _single_series(ex):
        for s in sentences:
            for m in _RATIO_RE.finditer(s):
                if m.group("twice"):
                    lo = hi = 2.0
                elif m.group("lo"):
                    lo, hi = sorted((_num(m.group("lo")), _num(m.group("hi"))))
                elif m.group("n"):
                    lo = hi = _num(m.group("n"))
                else:
                    lo, hi = sorted((_frac(m.group("flo")), _frac(m.group("fhi") or m.group("flo"))))
                ments = _mentions(s, pts)
                # Subject: what the sentence names before its first ratio ("The bar at 7 is the tallest,
                # about twice as tall as ... and six times as tall as ..."); object: the first thing after.
                first_ratio = next(_RATIO_RE.finditer(s)).start()
                subj = [(v, lab) for a, e, v, lab in ments if e <= first_ratio]
                before = [v for v, lab in subj if lab] or [v for v, _ in subj]
                nxt = next((x.start() for x in _RATIO_RE.finditer(s, m.end())), len(s))
                after = [(a, v) for a, e, v, _ in ments if m.end() <= a < nxt]
                if not before or not after:
                    continue
                first = after[0][0]
                obj = [v for a, v in after if a - first < 25]  # "the bars at 6 and 8": a group
                b = sum(obj) / len(obj)
                if b <= 0:
                    continue
                r = (sum(before) / len(before)) / b
                if not (lo / (1 + RATIO_TOL) <= r <= hi * (1 + RATIO_TOL)):
                    issues.append(Issue(kind="wrong_comparison", source="code",
                                        detail=f"'{m.group(0)}' in \"{s.strip()[:120]}\": the independent "
                                               f"reading gives a ratio of about {r:.2g}."))
    if len(ex.boxes) == 2:
        issues += _box_comparisons(sentences, ex)
    return issues


_BOX_CLAIMS = [  # (pattern, measure, True if the claim says the subject's measure is bigger)
    (r"\bwider box\b|\bbox is wider\b", lambda b: b.q3 - b.q1, True),
    (r"\bnarrower box\b|\bbox is narrower\b", lambda b: b.q3 - b.q1, False),
    (r"\blonger left whisker\b", lambda b: b.q1 - b.min, True),
    (r"\bshorter left whisker\b", lambda b: b.q1 - b.min, False),
    (r"\blonger right whisker\b", lambda b: b.max - b.q3, True),
    (r"\bshorter right whisker\b", lambda b: b.max - b.q3, False),
    (r"\bhigher median\b|\bmedian is higher\b|\blarger median\b", lambda b: b.median, True),
    (r"\blower median\b|\bmedian is lower\b|\bsmaller median\b", lambda b: b.median, False),
    (r"\b(?:broader|wider|larger|greater) (?:overall )?range\b|\bspans? a (?:broader|wider|larger) range\b",
     lambda b: b.max - b.min, True),
]


def _box_comparisons(sentences: list[str], ex: ExtractedValues) -> list[Issue]:
    """Two boxes: "the upper plot has a longer left whisker" is checked from the five numbers.
    Only sentences that name one of the boxes (by name or position) are checked."""
    issues, boxes = [], ex.boxes
    refs = [(r"\b(upper|top)\s+(box|plot|one)", 0), (r"\b(lower|bottom)\s+(box|plot|one)", 1)]
    refs += [(r"\b" + re.escape(b.name) + r"\b", i) for i, b in enumerate(boxes) if b.name]
    vals = [v for b in boxes for v in (b.min, b.q1, b.median, b.q3, b.max) if v is not None]
    btol = max(0.04 * (max(vals) - min(vals)), 1e-9) if vals else 0
    for s in sentences:
        found = {i for pat, i in refs if re.search(pat, s, re.I)}
        if len(found) != 1:
            continue  # no box named, or both: the subject isn't clear
        subj = found.pop()
        for pat, measure, bigger in _BOX_CLAIMS:
            if not re.search(pat, s, re.I):
                continue
            try:
                diff = measure(boxes[subj]) - measure(boxes[1 - subj])
            except TypeError:  # a part isn't drawn
                continue
            if (diff < -btol) if bigger else (diff > btol):
                issues.append(Issue(kind="wrong_comparison", source="code",
                                    detail=f"\"{s.strip()[:120]}\": the independent reading of the two boxes "
                                           "says the opposite."))
    return issues


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
                issues.append(Issue(kind="wrong_number", source="code", label=row.label,
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
                    issues.append(Issue(kind="wrong_number", source="code", label=d.label, series=d.series,
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
                issues.append(Issue(kind="wrong_number", source="code", label=row.label,
                                    detail=f"'{row.label}': description says {row.value:g}, but the "
                                           f"independent reading has {', '.join(f'{v:g}' for v in options)}."))
            continue
        rs = mapping.get(row.series)
        if (k, rs) in ref and abs(row.value - ref[(k, rs)]) > tol:
            issues.append(Issue(kind="wrong_number", source="code", label=row.label, series=row.series,
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
