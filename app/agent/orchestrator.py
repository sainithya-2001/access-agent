"""M5: the agent loop for one figure.

1. First pass (code, predictable): classify → extract → describe → verify.
   Pass → auto-approved, no agent call (cheap path).
2. Fail → the agent (Muse Spark) chooses actions until the figure is approved, marked decorative,
   or escalated to a human. Hard caps: MAX_ACTIONS and MAX_REWRITES.

Actions are JSON ({"action", "args", "why"}) rather than native function calling, so the same loop
runs on the Meta API and on Muse Spark Open (Baseten). The model chooses; code executes and
enforces the rules (e.g. approve is refused unless the last verification passed).

Every step is recorded as a TraceStep: what happened, in plain language. This is shown in the UI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
from pydantic import BaseModel, ValidationError

from app.agent import tools
from app.llm import chat
from app.models import (BBox, Description, ExtractedValues, FigureResult, Region, Status,
                        TraceStep)

MAX_ACTIONS = 8
MAX_REWRITES = 2
ZOOM_DPI = 300
RECROP_PT = 30
COLOR_ONLY_REASON = ("Series are distinguished by colour only (no legend or labels): check which "
                     "series is which, e.g. from the text or tables nearby, and name them in the description")
NO_SCALE_REASON = "values can't be read: no numeric scale"


class Action(BaseModel):
    action: str
    args: dict = {}
    why: str = ""


class FigureAgent:
    def __init__(self, region: Region, pdf_path: str, backend: str | None = None,
                 start_description: Description | None = None):
        """start_description: begin from a given (e.g. deliberately wrong) description; for demos/tests."""
        self.res = FigureResult(region=region)
        self.pdf_path = pdf_path
        self.backend = backend
        self.start_description = start_description
        self.rewrites = 0
        self.actions = 0
        self.history: list[str] = []

    # ---------- trace ----------
    def log(self, action: str, summary: str) -> None:
        self.res.trace.append(TraceStep(step=len(self.res.trace) + 1, action=action, summary=summary))

    @staticmethod
    def _issues_text(v) -> str:
        if not v or not v.issues:
            return "none"
        return "\n".join(f"- {i.kind}: {i.detail}" for i in v.issues)

    def _verdict(self) -> str:
        v = self.res.verification
        s = f"{'PASS' if v.passed else 'FAIL'} ({v.confidence:.2f})"
        if v.issues:
            s += ": " + "; ".join(f"{i.kind}: {i.detail[:90]}" for i in v.issues[:3])
        return s

    # ---------- steps ----------
    def _classify(self) -> bool:
        c = tools.classify_region(self.res.region, self.backend)
        self.res.classification = c
        self.log("classify", f"Classified as {c.type.value}" + (" (decorative)" if c.is_decorative else "") +
                 (f": {c.reason}" if c.reason else ""))
        return not c.is_decorative

    def _extract(self) -> None:
        kind = self.res.classification.type.value
        self.res.extracted = tools.extract_values(self.res.region, kind, self.backend)
        ex = self.res.extracted
        n = len([d for d in ex.data if d.value is not None])
        self.log("extract_values", f"Read the figure independently: {len(ex.labels)} labels"
                 + (f", {n} values ({'estimated from axis' if ex.values_estimated else 'printed'})" if n else ""))

    def _describe(self) -> None:
        if self.start_description:  # test mode: used once, for the first description
            self.res.description, self.start_description = self.start_description, None
            self.log("describe", "Started from a provided description (test mode)")
        else:
            self.res.description = tools.describe(self.res.region, self.res.classification.type.value,
                                                  self.backend, no_scale=self._no_scale())
            self.log("describe", f"Wrote description: \"{self.res.description.alt_text}\"")

    def _verify(self) -> None:
        self.res.verification = tools.verify(self.res.region, self.res.description,
                                             self.res.extracted, self.backend)
        self.log("verify", self._verdict())

    def _zoom(self) -> str:
        r = self.res.region
        out = Path(r.crop_path).with_name(Path(r.crop_path).stem + "_zoom.png")
        page = fitz.open(self.pdf_path)[r.page - 1]
        b = r.bbox
        page.get_pixmap(clip=fitz.Rect(b.x0 - 6, b.y0 - 6, b.x1 + 6, b.y1 + 6) & page.rect,
                        dpi=ZOOM_DPI).save(out)
        return str(out)

    def _recrop(self, side: str) -> None:
        r = self.res.region
        page = fitz.open(self.pdf_path)[r.page - 1]
        b = r.bbox
        d = RECROP_PT
        x0 = b.x0 - (d if side in ("left", "all") else 0)
        x1 = b.x1 + (d if side in ("right", "all") else 0)
        y0 = b.y0 - (d if side in ("top", "all") else 0)
        y1 = b.y1 + (d if side in ("bottom", "all") else 0)
        rect = fitz.Rect(x0, y0, x1, y1) & page.rect
        r.bbox = BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)
        out = Path(r.crop_path).with_name(Path(r.crop_path).stem + f"_recrop_{side}.png")
        page.get_pixmap(clip=rect, dpi=200).save(out)
        r.crop_path = str(out)
        self.res.description = None  # read and describe again from the new crop

    def _value_pair(self, label: str, series: str | None = None):
        """The independent reading's data point for `label` and the description's matching one.
        With several series at one label, use the series asked for, else the pair that disagrees most."""
        k = label.strip().lower()
        refs = [x for x in self.res.extracted.data if x.label.strip().lower() == k and x.value is not None]
        mine = [x for x in self.res.description.data_table if x.label.strip().lower() == k]
        if len(refs) <= 1:
            return (refs[0] if refs else None), (mine[0] if mine else None)
        mapping = tools._series_map([m for m in mine if m.series is not None and m.value is not None], refs)
        pairs = [(r, next((m for m in mine if mapping.get(m.series) == r.series), None)) for r in refs]
        if series:
            asked = [p for p in pairs if (p[0].series or "").lower() == str(series).lower()
                     or (p[1] and (p[1].series or "").lower() == str(series).lower())]
            pairs = asked or pairs
        return max(pairs, key=lambda p: abs(p[1].value - p[0].value) if p[1] and p[1].value is not None else -1)

    def _no_scale(self) -> bool:
        return tools.no_numeric_scale(self.res.extracted)

    def _color_only(self) -> bool:
        ex = self.res.extracted
        return ex.series_identified_by == "color_only" and tools.series_count(ex) >= 2

    # ---------- agent ----------
    def _decide(self) -> Action:
        d, ex, v = self.res.description, self.res.extracted, self.res.verification
        prompt = tools._prompt(
            "agent",
            caption=self.res.region.caption or "no caption",
            type=self.res.classification.type.value,
            alt_text=d.alt_text, long_description=d.long_description,
            estimated="estimated from the axis" if ex.values_estimated else "printed on the figure",
            extracted=json.dumps({"labels": ex.labels, "data": [x.model_dump() for x in ex.data],
                                  "trend": ex.trend, "labels_cut_off": ex.labels_cut_off}),
            verdict="PASS" if v.passed else "FAIL", confidence=f"{v.confidence:.2f}",
            issues=self._issues_text(v),
            history="\n".join(self.history) or "none",
            steps_left=MAX_ACTIONS - self.actions, rewrites_left=MAX_REWRITES - self.rewrites,
        )
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                  tools._image_part(self.res.region.crop_path)]}]
        text = chat(messages, backend=self.backend, step=f"agent_decide:{len(self.history)}")
        try:
            return Action.model_validate(tools._json(text))
        except (ValueError, ValidationError):
            return Action(action="escalate", args={"reason": "Agent reply could not be parsed"},
                          why="invalid agent output")

    def _finish(self, status: Status, reason: str | None = None) -> FigureResult:
        if status == Status.auto_approved and self.res.region.review_reason:
            status, reason = Status.needs_review, self.res.region.review_reason
        v = self.res.verification
        if status == Status.needs_review and v and any(i.kind == "no_numeric_scale" for i in v.issues) \
                and reason != NO_SCALE_REASON:
            reason = NO_SCALE_REASON + (f"; {reason}" if reason else "")
        self.res.status = status
        self.res.escalation_reason = reason
        label = {Status.auto_approved: "Auto-approved", Status.needs_review: "Sent to human review",
                 Status.approved: "Approved"}.get(status, status.value)
        self.log("finish", label + (f": {reason}" if reason else ""))
        return self.res

    def run(self) -> FigureResult:
        """Never raises: any tool failure sends the figure to human review with the reason."""
        try:
            return self._run()
        except Exception as e:  # unusable model output, network error, etc.
            self.log("error", f"{e.__class__.__name__}: {str(e)[:160]}")
            return self._finish(Status.needs_review, "A processing step failed; needs a human check")

    def _run(self) -> FigureResult:
        if not self._classify():
            self.res.description = Description(alt_text="", long_description="")
            return self._finish(Status.auto_approved, "decorative image, empty alt text")

        # Extract first, so a cut-off crop is widened before any description is written.
        self._extract()
        if self.res.extracted.labels_cut_off:
            self.log("recrop", "Labels look cut off at the crop edge → widening the crop")
            self._recrop("all")
            self._extract()
        self._describe()
        self._verify()
        if self._color_only():
            # A person must say which series is which; more agent steps can't fix that.
            self.log("escalate", "Two or more series are told apart only by colour, with no legend or "
                     "labels naming them → a person must say which series is which")
            return self._finish(Status.needs_review, COLOR_ONLY_REASON)
        if self._no_scale() and any(i.kind == "no_numeric_scale" for i in self.res.verification.issues):
            # Told to compare instead of estimating, it still gave numbers the page doesn't support.
            self.log("escalate", "The value axis has no numbers and the description still gives values "
                     "that aren't in the page text → a person must check them")
            return self._finish(Status.needs_review, NO_SCALE_REASON)
        if self.res.verification.passed:
            return self._finish(Status.auto_approved)

        while self.actions < MAX_ACTIONS:
            act = self._decide()
            self.actions += 1
            a, args = act.action, act.args or {}
            self.history.append(f"{self.actions}. {a} {json.dumps(args)} — {act.why}")
            why = f" ({act.why})" if act.why else ""

            if a == "reread_value":
                label = str(args.get("label", ""))
                ref, mine = self._value_pair(label, args.get("series"))
                if not ref:
                    self.log("reread_value", f"Agent asked to re-read '{label}', but no such data point{why}")
                    continue
                rr = tools.reread_value(self.res.region, label, mine.value if mine else "not stated",
                                        ref.value, ref.series, self.backend, zoom_path=self._zoom())
                old = ref.value
                ref.value = rr.value
                self.log("reread_value", f"Zoomed in on '{label}': read {rr.value:g} "
                         f"(was {old:g}; description says {mine.value if mine else '—'}){why}")
            elif a == "rewrite":
                if self.rewrites >= MAX_REWRITES:
                    return self._finish(Status.needs_review, "Rewrite limit reached; issues remain: "
                                        + self._issues_text(self.res.verification))
                self.rewrites += 1
                fb = args.get("feedback") or self._issues_text(self.res.verification)
                self.res.description = tools.describe(self.res.region, self.res.classification.type.value,
                                                      self.backend, feedback=fb, no_scale=self._no_scale())
                self.log("rewrite", f"Rewrote the description (attempt {self.rewrites}){why}")
            elif a == "recrop":
                side = args.get("side", "all")
                self._recrop(side if side in ("left", "right", "top", "bottom", "all") else "all")
                self.log("recrop", f"Widened the crop ({side}) and re-read the figure{why}")
                self._extract()
                self._describe()
            elif a == "verify":
                self._verify()
            elif a == "mark_decorative":
                self.res.description = Description(alt_text="", long_description="")
                return self._finish(Status.auto_approved, f"marked decorative: {args.get('reason', act.why)}")
            elif a == "approve":
                if self.res.verification.passed:
                    return self._finish(Status.auto_approved, act.why or None)
                self.log("approve", "Approval refused: the latest verification did not pass")
            elif a == "escalate":
                return self._finish(Status.needs_review, args.get("reason") or act.why)
            else:
                self.log(a, f"Unknown action '{a}' ignored")

            # Auto-verify after changes, so approval is always based on a fresh check.
            if a in ("reread_value", "rewrite", "recrop"):
                self._verify()
                if self.res.verification.passed:
                    return self._finish(Status.auto_approved, "passed verification after " + a)

        return self._finish(Status.needs_review, "Action limit reached; issues remain: "
                            + self._issues_text(self.res.verification))
