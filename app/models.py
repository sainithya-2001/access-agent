"""Data models for the whole pipeline. Every model output is validated against these."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------- Ingest ----------

class BBox(BaseModel):
    """Box in PDF points (1/72 inch), origin top-left of the page."""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class TextBlock(BaseModel):
    text: str
    bbox: BBox
    font_size: float | None = None  # largest span size
    is_bold: bool = False  # any span bold
    main_size: float | None = None  # size of most characters (headings vs body)
    bold_share: float = 0.0  # fraction of characters in bold (1.0 = a bold label/heading line)


class EmbeddedImage(BaseModel):
    xref: int
    bbox: BBox
    path: str  # saved crop on disk


class Page(BaseModel):
    number: int  # 1-based
    width: float
    height: float
    image_path: str  # full-page render
    text_blocks: list[TextBlock] = []
    embedded_images: list[EmbeddedImage] = []
    is_scanned: bool = False  # no usable text layer → needs OCR


class Document(BaseModel):
    id: str
    source_path: str
    title: str | None = None
    pages: list[Page] = []


# ---------- Figures and the agent ----------

class RegionType(str, Enum):
    chart = "chart"
    diagram = "diagram"
    photo = "photo"
    equation = "equation"
    table_image = "table_image"
    text_image = "text_image"
    decorative = "decorative"
    other = "other"


class StemLeaf(BaseModel):
    series: dict[str, list[float]]  # series name → every data value, sorted
    leaf_unit: float = 1.0  # value = (stem * 10 + leaf) * leaf_unit
    key: str | None = None  # e.g. "Key: 8|0 means 80."
    unit_source: str = "assumed"  # "key" | "text" (values found in the page text) | "assumed"


class Region(BaseModel):
    id: str
    page: int
    kind: str = "figure"  # "figure" | "table"
    bbox: BBox  # PDF points
    crop_path: str
    source: str  # "embedded" | "vector" | "table" | "sam" | "spark_check"
    confidence: float | None = None  # detector confidence when available (SAM)
    caption: str | None = None  # e.g. "Figure 2.10" found near the region
    nearby_text: str = ""
    table_rows: list[list[str | None]] | None = None  # text tables: header row first
    stem_leaf: StemLeaf | None = None  # stemplot tables, expanded to exact values in code
    review_reason: str | None = None  # detection is unsure (e.g. unconfirmed box): never auto-approve


class Classification(BaseModel):
    type: RegionType
    is_decorative: bool = False
    reason: str = ""


class DataPoint(BaseModel):
    label: str
    value: float | None = None
    series: str | None = None


class BoxStats(BaseModel):
    """One box of a box plot, read off the number line. A part that isn't drawn is None
    (e.g. a plain line with no box has only min and max)."""
    name: str | None = None
    min: float | None = None
    q1: float | None = None
    median: float | None = None
    q3: float | None = None
    max: float | None = None
    median_drawn: bool | None = None  # a median line is drawn inside (or on the edge of) the box


class ExtractedValues(BaseModel):
    """Read from the image independently of the description; used to check it."""
    title: str | None = None
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    labels: list[str] = []  # every visible text label
    data: list[DataPoint] = []  # charts only
    values_estimated: bool = False  # True if values were read off the axis (not printed)
    trend: str | None = None  # one sentence, e.g. "rises from 184 to 230 with a dip at year 7"
    labels_cut_off: bool = False  # crop missed axes/legend → recrop
    # How a reader tells the data series apart. "color_only" with 2+ series → human review:
    # a blind student can't use colour, and without a legend nobody can say which line is which.
    series_identified_by: Literal["single_series", "legend", "direct_labels", "color_only",
                                  "other"] | None = None
    # Whether each axis has numeric tick labels (None: no such axis), and which axis the values
    # are measured on. Values read off an axis without numbers are guesses (see tools.no_numeric_scale).
    x_axis_numeric_scale: bool | None = None
    y_axis_numeric_scale: bool | None = None
    value_axis: Literal["x", "y"] | None = None
    boxes: list[BoxStats] = []  # box plots only

    @field_validator("value_axis", mode="before")
    @classmethod
    def _axis(cls, v):  # "Y", "y-axis", "both", "none" → "y", "y", None, None
        v = str(v or "").strip().lower()[:1]
        return v if v in ("x", "y") else None


class Description(BaseModel):
    alt_text: str = Field(max_length=300)
    long_description: str = ""
    data_table: list[DataPoint] = []  # charts only
    latex: str | None = None  # equations only


class Issue(BaseModel):
    kind: str  # e.g. "wrong_number", "missing_label", "invented_detail", "wrong_trend"
    detail: str
    source: str = "model"  # "model" (verifier LLM) or "code" (deterministic check)
    # The data point a wrong_number is about (code checks), so the agent can re-read exactly that value.
    label: str | None = None
    series: str | None = None


class Verification(BaseModel):
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[Issue] = []


class Status(str, Enum):
    pending = "pending"
    auto_approved = "auto_approved"
    needs_review = "needs_review"
    approved = "approved"  # by a human
    edited = "edited"  # by a human
    rejected = "rejected"


class TraceStep(BaseModel):
    """One human-readable agent decision. Shown in the UI."""
    step: int
    action: str  # tool name or decision
    summary: str  # e.g. "Description said 4 bars; image has 5 → rewriting"


class FigureResult(BaseModel):
    region: Region
    classification: Classification | None = None
    extracted: ExtractedValues | None = None
    description: Description | None = None
    verification: Verification | None = None
    status: Status = Status.pending
    escalation_reason: str | None = None
    trace: list[TraceStep] = []
