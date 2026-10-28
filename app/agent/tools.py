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
import json
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import chat
from app.models import (Classification, Description, ExtractedValues, Issue, Region,
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


def _ask(model: type[T], prompt: str, image: str, step: str, backend: str | None) -> T:
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(image)]}]
    text = chat(messages, backend=backend, step=step)
    try:
        return model.model_validate(_json(text))
    except (ValueError, ValidationError) as e:
        # One repair attempt, telling the model what was wrong.
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not valid JSON for the schema ({e}). "
                                                 "Reply again with ONLY the corrected JSON."}]
        text = chat(messages, backend=backend, step=step + "_repair")
        return model.model_validate(_json(text))


# ---------- tools ----------

def classify_region(region: Region, backend: str | None = None) -> Classification:
    p = _prompt("classify", caption=region.caption, nearby_text=region.nearby_text)
    return _ask(Classification, p, region.crop_path, "classify", backend)


def extract_values(region: Region, kind: str, backend: str | None = None) -> ExtractedValues:
    p = _prompt("extract_values", type=kind)
    return _ask(ExtractedValues, p, region.crop_path, "extract_values", backend)


def describe(region: Region, kind: str, backend: str | None = None,
             feedback: str | None = None) -> Description:
    """feedback: issues from a failed verification, used when the agent rewrites."""
    p = _prompt("describe", type=kind, caption=region.caption, nearby_text=region.nearby_text)
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
    result = _ask(Verification, p, region.crop_path, "verify", backend)
    for issue in result.issues:
        issue.source = "model"

    code_issues = code_checks(desc, extracted)
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
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def code_checks(desc: Description, ex: ExtractedValues) -> list[Issue]:
    issues: list[Issue] = []

    if len(desc.alt_text) > ALT_MAX:
        issues.append(Issue(kind="alt_text_problem", source="code",
                            detail=f"Alt text is {len(desc.alt_text)} characters (max {ALT_MAX})."))

    # Data table vs independently extracted values, matched by label.
    ref = {d.label.strip().lower(): d.value for d in ex.data if d.value is not None}
    span = (max(ref.values()) - min(ref.values())) if len(ref) > 1 else max([abs(v) for v in ref.values()] or [1])
    biggest = max([abs(v) for v in ref.values()] or [1])
    # Values read off an axis carry reading error; printed values should match almost exactly.
    # (Tuned on Figure 2.10: a true 201.6 vs an axis reading of 200 must not fail;
    #  a planted 232 vs 207 must.)
    tol = max(0.04 * span, 0.01 * biggest) if ex.values_estimated else 0.005 * max(span, 1)
    for row in desc.data_table:
        key = row.label.strip().lower()
        if key in ref and row.value is not None and abs(row.value - ref[key]) > max(tol, 1e-9):
            issues.append(Issue(kind="wrong_number", source="code",
                                detail=f"'{row.label}': description says {row.value}, "
                                       f"independent reading says {ref[key]}."))

    # Numbers in the text that appear nowhere in the figure (possible invented values).
    known = set(_numbers(" ".join(ex.labels + [ex.title or "", ex.trend or ""])))
    known |= {d.value for d in ex.data if d.value is not None}
    if known:
        for n in set(_numbers(desc.long_description)):
            close = any(abs(n - k) <= max(tol, 0.01 * max(abs(k), 1)) for k in known)
            if not close and not (1 <= n <= 12 and n == int(n)):  # ignore small counts ("3 bars")
                issues.append(Issue(kind="invented_detail", source="code",
                                    detail=f"The number {n:g} is in the description but not in the figure."))
    return issues
