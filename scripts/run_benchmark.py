"""M8: run the full agent on every benchmark figure and score it against the checked truth.

  python scripts/run_benchmark.py --backend meta --set dev
  python scripts/run_benchmark.py --backend baseten --set dev
  python scripts/run_benchmark.py --backend meta --set heldout     # ONCE, for the final report (CLAUDE.md rule 8)

Detection uses the free layers only (no page check), so every backend sees the same regions.
Per item it records status, description, trace, model calls, tokens, cost and time, then scores:

  correct outcome   auto_approve items must be auto-approved; needs_review_ok items are correct when
                    sent to review, or when auto-approved with an accurate description
  value accuracy    each true value matched to the description's data table by series and label;
                    correct within 0.5% (values printed on the figure) or 3% of the axis range
                    (values read off the axis); title and axis titles must be mentioned
  invented content  numbers in the description that are not true values, labels, ticks, or in the
                    reference; data-table labels that match no true label
  unsafe approval   auto-approved with any wrong number or invented content (headline metric, target 0)
  review effort     share of figures sent to a person (vs 100% when everything is reviewed)

Items without true values are graded by a judge model (muse-wonder-1.0, on the Meta API) against the
checked reference description; reported separately as "judged by model". The stemplot is expanded
in code (app/stemplot.py) and compared exactly. Results: eval/results/<set>_<backend>.json.
"""

from __future__ import annotations

import argparse
import itertools
import os
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace  # noqa: E402

from pydantic import BaseModel, ValidationError  # noqa: E402

from app.agent import tools  # noqa: E402
from app.agent.orchestrator import FigureAgent  # noqa: E402
from app.detect import detect  # noqa: E402
from app.ingest import ingest  # noqa: E402
from app import llm  # noqa: E402
from app.llm import LOG_PATH, chat, get_client  # noqa: E402
from app.models import DataPoint, Description  # noqa: E402

JUDGE_MODEL = "muse-wonder-1.0"
# USD per million tokens (input, output): list prices as of Oct 2026, from third-party trackers;
# Meta's and Baseten's own pricing pages were not checked. Cached responses cost nothing.
PRICES = {
    "muse-spark-1.1": (1.25, 4.25),
    "meta-models/muse-spark-open-1026": (1.25, 4.25),
    "muse-wonder-1.0": (4.00, 20.00),
}
PRINTED_TOL = 0.005   # values printed on the figure: within 0.5% of the true value
AXIS_TOL = 0.03       # values read off the axis: within 3% of the axis range
# Items whose values are printed on the figure (from the benchmark notes: "Values are printed on the bars").
PRINTED = {"fig-2.5"}
STATS = [("min", r"\b(min|minimum|lowest|smallest)\b"), ("q1", r"\b(q1|first quartile|lower quartile|25th)\b"),
         ("median", r"\b(median|q2|middle)\b"), ("q3", r"\b(q3|third quartile|upper quartile|75th)\b"),
         ("max", r"\b(max|maximum|highest|largest)\b")]
# A description may name the drawn part instead ("left whisker end", "right edge of box"). Tried after
# STATS, box edges before whisker ends. Written here, not imported from the agent, so a mapping bug in
# the agent can't hide itself in the score.
PARTS = [("median", r"\b(dashed line|line inside)\b"),
         ("q1", r"\b(left|lower|bottom)\b.*\b(edge|side)\b|\bbox\b.*\b(left|lower|bottom|start)\b"),
         ("q3", r"\b(right|upper|top)\b.*\b(edge|side)\b|\bbox\b.*\b(right|upper|top|end)\b"),
         ("min", r"\b(left|lower|bottom)\b.*\b(whisker|end|tip)\b|\bwhisker\b.*\b(left|lower|bottom)\b"),
         ("max", r"\b(right|upper|top)\b.*\b(whisker|end|tip)\b|\bwhisker\b.*\b(right|upper|top)\b")]
PART_WORDS = {"of", "the", "box", "edge", "side", "whisker", "end", "tip", "line", "inside", "dashed",
              "left", "right", "upper", "lower", "top", "bottom"}


# ---------- running ----------

def own_calls(lines: list[dict]) -> list[dict]:
    """This process's calls only: parallel runs append to the same log."""
    return [c for c in lines if c.get("pid") == os.getpid()]


def log_lines() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out = []
    for x in open(LOG_PATH, encoding="utf-8"):
        try:
            out.append(json.loads(x))
        except ValueError:  # a damaged line (e.g. from before log writes were locked): skip it
            continue
    return out


_STEP_AVG: dict = {}


def step_averages() -> dict:
    """Mean tokens per (model, step) over every uncached call in the log: the estimate for cached
    calls whose original tokens were not recorded (cache entries from before Oct 28)."""
    if not _STEP_AVG:
        acc = defaultdict(lambda: [0, 0, 0])
        for c in log_lines():
            if not c.get("cached") and c.get("input_tokens") is not None:
                a = acc[(c.get("model"), step_name(c["step"]))]
                a[0] += c["input_tokens"]; a[1] += c.get("output_tokens") or 0; a[2] += 1
        _STEP_AVG.update({k: (v[0] / v[2], v[1] / v[2]) for k, v in acc.items()})
    return _STEP_AVG


def step_name(step: str) -> str:
    return step.split(":")[0].replace("_repair", "")


def usd(model: str, i: float, o: float) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (i * pin + o * pout) / 1e6


def cost_of(calls: list[dict]) -> dict:
    """usd: spent in this run (cached calls are free). usd_fresh: what the same calls cost without the
    cache, from the original call's tokens, else the average for that model and step (estimated)."""
    out = {"calls": len(calls), "uncached": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0,
           "usd_fresh": 0.0, "fresh_estimated_calls": 0, "model_seconds": 0.0}
    for c in calls:
        model = c.get("model")
        if not c.get("cached"):
            i, o = c.get("input_tokens") or 0, c.get("output_tokens") or 0
            out["uncached"] += 1
            out["input_tokens"] += i
            out["output_tokens"] += o
            out["usd"] += usd(model, i, o)
            out["usd_fresh"] += usd(model, i, o)
            out["model_seconds"] += c.get("seconds") or 0
            continue
        orig = c.get("original")
        if orig and orig.get("input_tokens") is not None:
            out["usd_fresh"] += usd(model, orig["input_tokens"], orig.get("output_tokens") or 0)
        else:
            avg = step_averages().get((model, step_name(c["step"])))
            if avg:
                out["usd_fresh"] += usd(model, *avg)
            out["fresh_estimated_calls"] += 1
    for k in ("usd", "usd_fresh"):
        out[k] = round(out[k], 4)
    out["model_seconds"] = round(out["model_seconds"], 1)
    return out


def heldout_spans(gap: int = 20) -> list[tuple[int, int]]:
    """The held-out set's page spans (pages less than `gap` apart joined): pages a dev run must not touch."""
    pages = sorted({f["page"] for f in json.loads((ROOT / "eval" / "heldout.json").read_text(encoding="utf-8"))["figures"]})
    spans: list[list[int]] = []
    for p in pages:
        if spans and p - spans[-1][1] < gap:
            spans[-1][1] = p
        else:
            spans.append([p, p])
    return [(a, b) for a, b in spans]


def find_region(item: dict, regions):
    by_id = {r.id: r for r in regions}
    if item.get("region_id") in by_id:
        return by_id[item["region_id"]]
    label = re.match(r"\s*((Figure|Table)\s*\d+\.\d+)", item["caption"])
    for r in regions:
        if r.page == item["page"] and r.caption and label and r.caption.startswith(label.group(1)):
            return r
    return None


class Judgement(BaseModel):
    factual_errors: list[dict] = []
    missing: list[str] = []
    accurate: bool


def judge(item: dict, res, page_text: str = "") -> Judgement:
    """Compare with the checked reference, the figure image and the page text; always on the Meta API,
    at temperature 0."""
    d = res.description
    ref = item.get("reference") or {}
    prompt = (ROOT / "app" / "agent" / "prompts" / "judge.md").read_text(encoding="utf-8")
    fields = {"caption": item["caption"], "type": item["type"], "notes": item.get("notes") or "none",
              "ref_alt": ref.get("alt_text") or "(none)", "ref_long": ref.get("long_description") or "(none)",
              "alt": (d.alt_text if d else "") or "(empty: marked decorative)",
              "long": (d.long_description if d else "") or "(none)", "page_text": page_text or "(none)"}
    for k, v in fields.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    # With the image, so a true detail the reference leaves out is not counted as an error.
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                             tools._image_part(res.region.crop_path)]}]
    text = chat(messages, backend="meta", step="judge", model=JUDGE_MODEL, temperature=0)
    try:
        return Judgement.model_validate(tools._json(text))
    except (ValueError, ValidationError) as e:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not valid JSON for the schema ({e}). Reply with ONLY the JSON."}]
        return Judgement.model_validate(tools._json(chat(messages, backend="meta", step="judge_repair",
                                                         model=JUDGE_MODEL, temperature=0)))


# ---------- scoring helpers ----------

def num(s: str) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def rng(s: str) -> tuple[float, float] | None:
    m = re.fullmatch(r"\s*(-?[\d.,]+)\s*(?:–|—|-|to)\s*(-?[\d.,]+)\s*%?\s*", str(s))
    return (num(m.group(1)), num(m.group(2))) if m else None


def labels_match(a: str, b: str, tol: float) -> bool:
    """Same data label: equal numbers, ranges with close endpoints, or mostly the same words."""
    na, nb = num(a), num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= max(tol, 1e-9)
    ra, rb = rng(a), rng(b)
    if ra and rb and None not in ra + rb:
        return abs(ra[0] - rb[0]) <= tol and abs(ra[1] - rb[1]) <= tol
    # A histogram bin given as its range ("0.5 to 1.5") is the bar labelled by its midpoint ("1").
    for r, n in ((ra, nb), (rb, na)):
        if r and None not in r and n is not None:
            return abs((r[0] + r[1]) / 2 - n) <= max(tol, 0.1 * abs(r[1] - r[0]))
    # "BMW 3 series" is not "BMW 5 series": numbers in both labels must be the same numbers
    # (compared whole: word-splitting would turn "0.5 to 1.5" into 0, 5 and 1 and match "1").
    da, db = set(tools._numbers(a)), set(tools._numbers(b))
    if da and db and da != db:
        return False
    wa, wb = tools._words(a), tools._words(b)
    if not (wa and wb):
        return False
    # A word matches its abbreviation on the chart ("Wash DC" vs "Washington DC").
    same = sum(any(x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)))
                   for y in wb) for x in wa)
    return same / min(len(wa), len(wb)) >= 0.6


def axis_range(item: dict, values: list[float], from_zero: bool = True) -> tuple[float, str]:
    """The smallest 'from X to Y' range in the checked reference that contains every true value;
    else the span of the values (from 0 for bar/point values, min to max for box plots)."""
    text = (item.get("reference") or {}).get("long_description") or ""
    cands = []
    for m in re.finditer(r"from\s+(-?\d[\d,]*(?:\.\d+)?)%?\s+to\s+(-?\d[\d,]*(?:\.\d+)?)", text):
        a, b = num(m.group(1)), num(m.group(2))
        if a is not None and b is not None and b > a and values and \
                min(values) >= a - 0.01 * (b - a) and max(values) <= b + 0.01 * (b - a):
            cands.append((b - a, f"reference: from {m.group(1)} to {m.group(2)}"))
    if cands:
        return min(cands)
    lo = (min(min(values), 0) if from_zero else min(values)) if values else 0
    return (max(values) - lo if values else 1.0) or 1.0, "span of the true values"


def true_points(item: dict) -> list[dict]:
    """Every true value as {series, label, value}; box plots as stat labels per box."""
    d = item.get("data") or {}
    out = []
    for i, s in enumerate(d.get("series", [])):
        for p in s["points"]:
            if p.get("value") is not None:
                out.append({"series": s.get("name") or f"series {i + 1}", "label": str(p["label"]),
                            "value": float(p["value"])})
    for i, bx in enumerate(d.get("boxes", [])):
        for stat, _ in STATS:
            if bx.get(stat) is not None:
                out.append({"series": f"box:{bx.get('name') or i + 1}", "label": stat, "value": float(bx[stat])})
    return out


def desc_points(res, boxes: bool) -> list[dict]:
    """The description's data table; for box plots, labels normalized to min/q1/median/q3/max and the
    box taken from the series or from the rest of the label ("top min" → box "top")."""
    out = []
    for p in (res.description.data_table if res.description else []):
        if p.value is None:
            continue
        label, series = p.label, p.series
        if boxes:
            low = p.label.lower()
            hit = next(((s, pat) for s, pat in STATS + PARTS if re.search(pat, low)), None)
            if hit:
                rest = " ".join(w for w in re.sub(hit[1], " ", low).split() if w not in PART_WORDS).strip(" -:,")
                label, series = hit[0], series or rest or None
        out.append({"series": series, "label": label, "value": float(p.value), "raw_label": p.label})
    return out


def score_values(item: dict, res) -> dict:
    truth = true_points(item)
    if not truth:
        return {}
    has_boxes = bool((item.get("data") or {}).get("boxes"))
    mine = desc_points(res, has_boxes)
    printed = item["id"] in PRINTED

    # Each box-plot box / series of the truth vs each series of the description.
    t_groups = defaultdict(list)
    for t in truth:
        key = "box" if has_boxes and t["series"].startswith("box:") else "series"
        t_groups[(key, t["series"])].append(t)
    m_groups = defaultdict(list)
    for m in mine:
        m_groups[m["series"]].append(m)

    def pairs_for(tg: list[dict], mg: list[dict]) -> list[tuple[dict, dict]]:
        out, used = [], set()
        for t in tg:
            for j, m in enumerate(mg):
                if j not in used and labels_match(m["label"], t["label"], label_tol):
                    out.append((t, m)); used.add(j); break
        return out

    tkeys, mkeys = list(t_groups), list(m_groups)
    # Each truth group has its own axis: box plots sit on the number line, bars/points on the y-axis.
    spans = {tk: axis_range(item, [t["value"] for t in t_groups[tk]], from_zero=tk[0] != "box") for tk in tkeys}
    label_tol = 0.01 * max(sp for sp, _ in spans.values())

    def tol_for(tk):
        return lambda v: PRINTED_TOL * abs(v) if printed else AXIS_TOL * spans[tk][0]
    # Series names differ between the book and the description ("Final grades" vs "light blue line"):
    # pick the one-to-one mapping under which the most values are correct (then the most matched).
    best, best_score = {}, (-1, -1)
    if len(tkeys) == 1:
        merged = [m for k in mkeys for m in m_groups[k]]
        best = {tkeys[0]: merged}
    else:
        NONE = object()  # "this truth group gets no description series" (desc series may be named None)
        # A description series named like a truth series must be that series: the agreement mapping is
        # only for names the book doesn't use ("light blue line"), never to excuse swapped names.
        locked = {}
        for tk in tkeys:
            name = tk[1].split(":", 1)[1] if tk[1].startswith("box:") else tk[1]
            hits = [mk for mk in mkeys if mk is not None and labels_match(str(mk), name, 0)]
            if len(hits) == 1:
                locked[tk] = hits[0]
        perms = itertools.permutations(mkeys + [NONE] * len(tkeys), len(tkeys)) if len(mkeys) <= 6 else []
        taken = set(locked.values())
        for perm in perms:
            if any(locked[tk] != mk if tk in locked else mk in taken for tk, mk in zip(tkeys, perm)):
                continue
            mapping = {tk: (m_groups[mk] if mk is not NONE else []) for tk, mk in zip(tkeys, perm)}
            ps = [(tk, p) for tk in tkeys for p in pairs_for(t_groups[tk], mapping[tk])]
            sc = (sum(abs(m["value"] - t["value"]) <= tol_for(tk)(t["value"]) for tk, (t, m) in ps), len(ps))
            if sc > best_score:
                best, best_score = mapping, sc
    rows, correct, wrong = [], 0, 0
    matched_desc = set()
    for tk in tkeys:
        got = {id(t): m for t, m in pairs_for(t_groups[tk], best.get(tk, []))}
        for t in t_groups[tk]:
            m = got.get(id(t))
            if m is None:
                rows.append({**t, "described": None, "result": "missing"})
                continue
            matched_desc.add(id(m))
            ok = abs(m["value"] - t["value"]) <= tol_for(tk)(t["value"])
            correct += ok
            wrong += not ok
            rows.append({**t, "described": m["value"], "described_label": m["raw_label"],
                         "result": "correct" if ok else "wrong"})
    unmatched = [m["raw_label"] for m in mine if id(m) not in matched_desc
                 and not any(labels_match(m["label"], t["label"], label_tol) for t in truth)]
    # Rows left over with a true label (a second row for one label, or a series that was mapped to
    # nothing) must still agree with some true value at that label.
    tk_of = {id(t): tk for tk in tkeys for t in t_groups[tk]}
    conflicting = []
    for m in mine:
        if id(m) in matched_desc or m["raw_label"] in unmatched:
            continue
        if not any(labels_match(m["label"], t["label"], label_tol)
                   and abs(m["value"] - t["value"]) <= tol_for(tk_of[id(t)])(t["value"]) for t in truth):
            conflicting.append(f"{m['raw_label']} ({m['series']}): {m['value']:g}" if m["series"]
                               else f"{m['raw_label']}: {m['value']:g}")
    return {"true_values": len(truth), "correct": correct, "wrong": wrong, "conflicting_rows": conflicting,
            "missing": len(truth) - correct - wrong, "tolerance": "printed (0.5%)" if printed else
            "; ".join(f"{tk[1]}: 3% of {sp:g} ({src})" for tk, (sp, src) in spans.items()),
            "values": rows, "invented_labels": unmatched}


def score_mentions(item: dict, res) -> dict:
    d = res.description
    have = tools._words(f"{d.alt_text} {d.long_description}" if d else "")
    out = {}
    for field in ("title", "x_axis", "y_axis"):
        words = tools._words(item.get(field) or "")
        if words:
            out[field] = len(words & have) / len(words) >= tools.LABEL_MATCH
    return out


def nice_ticks(a: float, b: float) -> set[float]:
    """Every tick a 'nice' scale (steps 1, 2, 2.5, 5 x 10^n, 4 to 20 ticks) could put on this axis."""
    out = set()
    for step in (x * 10 ** e for e in range(-3, 6) for x in (1, 2, 2.5, 5)):
        if 4 <= (b - a) / step <= 20:
            out |= {round(k * step, 6) for k in range(int(a // step), int(b // step) + 2)}
            out.add(round(step, 6))  # "in increments of 5,000"
    return out


def score_invented(item: dict, res, values: dict, page_text: str = "") -> list[str]:
    """Numbers in the description that are nowhere in the truth, its labels and ticks, the reference,
    or (exactly) the page text the agent was given."""
    d = res.description
    if not d:
        return []
    text = re.sub(r"\b(Figure|Table|Example|Exercise)\s+\d+(\.\d+)*", " ", f"{d.alt_text}\n{d.long_description}")
    ref = item.get("reference") or {}
    allowed_text = " ".join(str(x or "") for x in (item.get("caption"), item.get("title"), item.get("x_axis"),
                                                   item.get("y_axis"), ref.get("alt_text"), ref.get("long_description")))
    allowed = set(tools._numbers(allowed_text))
    truth = true_points(item)
    for t in truth:
        allowed.add(t["value"])
        allowed |= set(tools._numbers(t["label"]))
    data = item.get("data") or {}
    allowed |= {float(v) for v in data.get("values", []) if isinstance(v, (int, float))}
    allowed |= {float(len(s["points"])) for s in data.get("series", [])}
    allowed |= {float(len(data.get("series", []))), float(len(data.get("boxes", [])))}
    for m in re.finditer(r"from\s+(-?\d[\d,]*(?:\.\d+)?)%?\s+to\s+(-?\d[\d,]*(?:\.\d+)?)", ref.get("long_description") or ""):
        a, b = num(m.group(1)), num(m.group(2))
        if a is not None and b is not None and b > a:
            allowed |= nice_ticks(a, b)
    groups = defaultdict(list)
    for t in truth:
        groups[t["series"]].append(t["value"])
    span = min(axis_range(item, v)[0] for v in groups.values()) if truth else None
    quoted = set(tools._numbers(page_text))
    out = []
    for n in sorted(set(tools._numbers(text))):
        if n in quoted:  # exact only: a number near a page number is not supported by it
            continue
        tol = (PRINTED_TOL * abs(n) if item["id"] in PRINTED else AXIS_TOL * span) if span else 0.005 * abs(n)
        if not any(abs(n - a) <= max(tol, 1e-9) for a in allowed):
            out.append(f"{n:g}")
    return out


def score_stemplot(item: dict, region) -> dict:
    got = sorted(v for vals in (region.stem_leaf.series.values() if region.stem_leaf else []) for v in vals)
    want = sorted(float(v) for v in item["data"]["values"])
    return {"true_values": len(want), "exact_match": got == want, "expanded": len(got),
            "missing": len([v for v in want if v not in got]), "extra": len([v for v in got if v not in want])}


# ---------- scoring a record ----------

def score_record(item: dict, rec: dict, which: str) -> None:
    """Score one saved result. Uses only what the record stores (description, data table, status,
    the judge's verdict, the stemplot comparison), so --rescore gives exactly what a fresh run would."""
    accurate, judged = None, False
    if rec.get("stemplot") is not None or rec.get("agent") is False:
        accurate = bool((rec.get("stemplot") or {}).get("exact_match"))
    elif which == "dev" and true_points(item):
        res = SimpleNamespace(description=Description(
            alt_text=rec.get("alt_text") or "", long_description=rec.get("long_description") or "",
            data_table=[DataPoint(**d) for d in rec.get("data_table") or []]))
        vals = score_values(item, res)
        rec["values"] = vals
        rec["mentions"] = score_mentions(item, res)
        rec["invented_numbers"] = score_invented(item, res, vals, rec.get("page_text") or "")
        rec["code_accurate"] = (vals["wrong"] == 0 and not vals["invented_labels"] and not vals.get("conflicting_rows")
                                and not rec["invented_numbers"])
        # The code checks the data table and stray numbers; the judge checks what the prose claims
        # (a wrong value in a sentence, a wrong comparison). Both must pass.
        j = rec.get("judge")
        rec["judge_accurate"] = None if j is None else bool(j["accurate"] and not j["factual_errors"])
        accurate = rec["code_accurate"] and rec["judge_accurate"] is not False
    elif which == "dev" and rec.get("judge"):
        judged = True
        accurate = rec["judge"]["accurate"] and not rec["judge"]["factual_errors"]
    rec["accurate"] = accurate
    auto = rec["status"] == "auto_approved"
    rec["sent_to_review"] = not auto
    if accurate is None:  # held-out: truth not recorded; outcome and effort only
        rec["correct_outcome"] = auto if rec["expected_outcome"] == "auto_approve" else None
        rec["unsafe"] = None
    else:
        rec["correct_outcome"] = auto if rec["expected_outcome"] == "auto_approve" else (not auto or accurate)
        rec["unsafe"] = auto and not accurate
    rec["scored_by"] = "judged by model" if judged else "code + judge" if rec.get("judge_accurate") is not None         else "code"


def results_path(which: str, backend: str, rep: int | None) -> Path:
    return ROOT / "eval" / "results" / (f"{which}_{backend}.json" if rep is None else f"{which}_{backend}_r{rep}.json")


def rescore(which: str, backend: str, rep: int | None = None) -> None:
    """Re-score saved results with the current scorer; no model calls (judge verdicts are reused)."""
    path = results_path(which, backend, rep)
    data = json.loads(path.read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "eval" / ("benchmark.json" if which == "dev" else "heldout.json")).read_text(encoding="utf-8"))
    items = {f["id"]: f for f in bench["figures"]}
    for rec in data["items"]:
        if rec.get("status") != "not_detected":
            for k in ("values", "mentions", "invented_numbers", "code_accurate", "judge_accurate"):
                rec.pop(k, None)
            score_record(items[rec["id"]], rec, which)
    data["summary"] = summarize(data["items"])
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_table(data["items"], data["summary"], SimpleNamespace(which=which, backend=backend))
    print(f"\nRe-scored {path.relative_to(ROOT)}")


# ---------- main ----------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["meta", "baseten"], required=True)
    p.add_argument("--set", choices=["dev", "heldout"], default="dev", dest="which")
    p.add_argument("--only", default=None, help="comma-separated item ids (development aid)")
    p.add_argument("--rescore", action="store_true", help="re-score saved results; no model calls")
    p.add_argument("--rep", type=int, default=None,
                   help="repeat number: own cache namespace, crop folder and results file (_rN)")
    args = p.parse_args()
    if args.rescore:
        return rescore(args.which, args.backend, args.rep)
    if args.rep:  # every repeat, the first included: never reuse answers from earlier runs
        llm.RUN_TAG = f"rep{args.rep}"
    # Own crop folder per backend and repeat, so runs can go in parallel (recrops overwrite files).
    out_root = ROOT / "output" / "bench" / f"{args.which}_{args.backend}_r{args.rep}" if args.rep else ROOT / "output"

    path = ROOT / "eval" / ("benchmark.json" if args.which == "dev" else "heldout.json")
    bench = json.loads(path.read_text(encoding="utf-8"))
    items = [f for f in bench["figures"] if not args.only or f["id"] in args.only.split(",")]
    pdf = ROOT / bench.get("source_pdf", "data/samples/introductory-statistics-2e_-_WEB.pdf")
    model = get_client(args.backend)[1]

    # Pages next to each figure too: a printed table there can supply exact values (app/source_tables.py).
    # A dev run never adds a page inside the held-out spans (168–175, 630–670) as a neighbour.
    own = {f["page"] for f in items}
    held = heldout_spans() if args.which == "dev" else []
    pages = sorted(own | {p + d for p in own for d in (-1, 1)
                          if p + d >= 1 and not any(a <= p + d <= b for a, b in held)})
    doc = ingest(pdf, ",".join(str(p) for p in pages), out_root=out_root)
    regions, _ = detect(doc, use_sam=False, use_check=False, out_root=out_root)  # free layers: same regions for every backend
    tables = [r for r in regions if r.kind == "table"]

    results = []
    for n, item in enumerate(items, 1):
        start, t0 = len(log_lines()), time.time()
        region = find_region(item, regions)
        rec = {"id": item["id"], "page": item["page"], "expected_outcome": item.get("expected_outcome")}
        if region is None:
            rec.update(status="not_detected", correct_outcome=False, unsafe=False)
            results.append(rec)
            print(f"{n:2}/{len(items)} {item['id']:10} not detected")
            continue
        rec["region_id"] = region.id

        if region.kind == "table":  # stemplot / text table: code only, no agent
            sp = score_stemplot(item, region) if (item.get("data") or {}).get("stems") else None
            rec.update(status="auto_approved" if region.stem_leaf else "needs_review", agent=False, stemplot=sp)
        else:
            res = FigureAgent(region, str(pdf), args.backend, tables=tables).run()
            rec.update(status=res.status.value, type=res.classification.type.value if res.classification else None,
                       alt_text=res.description.alt_text if res.description else None,
                       long_description=res.description.long_description if res.description else None,
                       data_table=[x.model_dump() for x in res.description.data_table] if res.description else [],
                       verifier={"passed": res.verification.passed, "confidence": res.verification.confidence,
                                 "issues": [i.model_dump() for i in res.verification.issues]}
                       if res.verification else None,
                       escalation_reason=res.escalation_reason, trace=[t.model_dump() for t in res.trace])
            rec["page_text"] = f"{region.caption or ''} {region.nearby_text}"
            if args.which == "dev":
                rec["judge"] = judge(item, res, rec["page_text"]).model_dump()
        score_record(item, rec, args.which)
        rec["usage"] = cost_of(own_calls(log_lines()[start:]))
        rec["seconds"] = round(time.time() - t0, 1)
        results.append(rec)
        flag = "UNSAFE " if rec["unsafe"] else ""
        print(f"{n:2}/{len(items)} {item['id']:10} {rec['status']:14} {flag}"
              f"{'correct' if rec['correct_outcome'] else 'WRONG' if rec['correct_outcome'] is False else '?'} outcome"
              f"  ({rec['scored_by']}, {rec['usage']['uncached']} uncached calls, {rec['seconds']}s)")

    summary = summarize(results)
    out = results_path(args.which, args.backend, args.rep)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"set": args.which, "backend": args.backend, "model": model,
                               "judge_model": JUDGE_MODEL, "rep": args.rep, "prices_usd_per_million": PRICES,
                               "summary": summary, "items": results}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print_table(results, summary, args)
    print(f"\nSaved {out.relative_to(ROOT)}")


def summarize(results: list[dict]) -> dict:
    n = len(results)
    code = [r for r in results if r.get("scored_by") in ("code", "code + judge")]
    judged = [r for r in results if r.get("scored_by") == "judged by model"]
    vals = [r["values"] for r in results if r.get("values")]
    usage = defaultdict(float)
    for r in results:
        for k, v in (r.get("usage") or {}).items():
            usage[k] += v
    sent = sum(1 for r in results if r.get("sent_to_review"))
    return {
        "items": n,
        "correct_outcome": sum(1 for r in results if r.get("correct_outcome")),
        "unsafe_approvals": sum(1 for r in results if r.get("unsafe")),
        # Items with true values: caught by the code checks, or passed them and caught by the judge.
        "unsafe_approvals_code_scored": sum(1 for r in code if r.get("unsafe") and not r.get("code_accurate")),
        "unsafe_approvals_judge_on_code_scored": sum(1 for r in code if r.get("unsafe") and r.get("code_accurate")),
        "unsafe_approvals_judged_by_model": sum(1 for r in judged if r.get("unsafe")),
        "auto_approved": sum(1 for r in results if r.get("status") == "auto_approved"),
        "sent_to_review": sent,
        "review_effort": round(sent / n, 3) if n else None,
        "review_effort_saved": round(1 - sent / n, 3) if n else None,
        "true_values": sum(v["true_values"] for v in vals),
        "values_correct": sum(v["correct"] for v in vals),
        "values_wrong": sum(v["wrong"] for v in vals),
        "values_missing": sum(v["missing"] for v in vals),
        "items_with_invented_content": sum(1 for r in results if r.get("invented_numbers") or
                                           (r.get("values") or {}).get("invented_labels")),
        "items_with_conflicting_rows": sum(1 for r in results if (r.get("values") or {}).get("conflicting_rows")),
        "judged_by_model": {"items": len(judged), "accurate": sum(1 for r in judged if r.get("accurate"))},
        "calls": int(usage["calls"]), "uncached_calls": int(usage["uncached"]),
        "input_tokens": int(usage["input_tokens"]), "output_tokens": int(usage["output_tokens"]),
        "cost_usd": round(usage["usd"], 4), "cost_usd_fresh_run": round(usage["usd_fresh"], 4),
        "fresh_cost_estimated_calls": int(usage["fresh_estimated_calls"]),
        "cost_per_figure_fresh": round(usage["usd_fresh"] / n, 4) if n else None,
        "model_seconds": round(usage["model_seconds"], 1),
        "wall_seconds": round(sum(r.get("seconds") or 0 for r in results), 1),
    }


def print_table(results: list[dict], s: dict, args) -> None:
    print(f"\n{'item':11}{'expected':16}{'status':15}{'outcome':9}{'values':12}{'invented':16}{'titles':8}"
          f"{'scored by':17}{'calls':>6}{'$':>8}{'sec':>7}")
    for r in results:
        v = r.get("values")
        vtxt = f"{v['correct']}/{v['true_values']}" + (f" ({v['wrong']}✗)" if v["wrong"] else "") if v else \
            ("stem ✓" if (r.get("stemplot") or {}).get("exact_match") else "stem ✗" if r.get("stemplot") else "—")
        inv = ",".join((r.get("invented_numbers") or []) + (v or {}).get("invented_labels", []))[:15] or "—"
        m = r.get("mentions") or {}
        mt = f"{sum(m.values())}/{len(m)}" if m else "—"
        if r.get("judge") and not v:
            vtxt = f"{len(r['judge']['factual_errors'])} errors"
        elif r.get("judge") and r["judge"]["factual_errors"]:
            vtxt += f" j{len(r['judge']['factual_errors'])}"
        out = "ok" if r.get("correct_outcome") else "WRONG" if r.get("correct_outcome") is False else "?"
        u = r.get("usage") or {}
        print(f"{r['id']:11}{r.get('expected_outcome') or '':16}{r['status']:15}{out:9}{vtxt:12}{inv:16}{mt:8}"
              f"{r.get('scored_by', ''):17}{u.get('uncached', 0):>6}{u.get('usd', 0):>8.3f}{r.get('seconds', 0):>7.0f}"
              + ("  UNSAFE" if r.get("unsafe") else ""))
    print(f"\n{args.which} set on {args.backend}: {s['items']} items")
    print(f"  Unsafe approvals:   {s['unsafe_approvals']}  (code checks {s['unsafe_approvals_code_scored']}, "
          f"judge on code-scored {s.get('unsafe_approvals_judge_on_code_scored', 0)}, "
          f"judged by model {s['unsafe_approvals_judged_by_model']})   target 0")
    print(f"  Correct outcome:    {s['correct_outcome']}/{s['items']}")
    if s["true_values"]:
        print(f"  Value accuracy:     {s['values_correct']}/{s['true_values']} correct, {s['values_wrong']} wrong, "
              f"{s['values_missing']} not in the data table")
    print(f"  Invented content:   {s['items_with_invented_content']} items")
    print(f"  Judged by model:    {s['judged_by_model']['accurate']}/{s['judged_by_model']['items']} accurate")
    print(f"  Review effort:      {s['sent_to_review']}/{s['items']} sent to a person ({s['review_effort']:.0%}; "
          f"vs 100% reviewing everything, {s['review_effort_saved']:.0%} saved)")
    print(f"  Cost this run:      {s['uncached_calls']} uncached of {s['calls']} calls, "
          f"{s['input_tokens']:,} in / {s['output_tokens']:,} out tokens, ${s['cost_usd']:.3f}; "
          f"{s['wall_seconds']:.0f}s wall")
    print(f"  Fresh-run cost:     ${s['cost_usd_fresh_run']:.3f} (${s['cost_per_figure_fresh']:.4f} per figure; "
          f"{s['fresh_cost_estimated_calls']} cached calls estimated from per-step averages)")


if __name__ == "__main__":
    main()
