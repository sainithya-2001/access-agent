"""Data models for the whole pipeline. Every model output is validated against these."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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
    font_size: float | None = None
    is_bold: bool = False


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


class Classification(BaseModel):
    type: RegionType
    is_decorative: bool = False
    reason: str = ""


class DataPoint(BaseModel):
    label: str
    value: float | None = None
    series: str | None = None


class ExtractedValues(BaseModel):
    """Read from the image independently of the description; used to check it."""
    title: str | None = None
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    labels: list[str] = []  # every visible text label
    data: list[DataPoint] = []  # charts only
    labels_cut_off: bool = False  # crop missed axes/legend → recrop


class Description(BaseModel):
    alt_text: str = Field(max_length=300)
    long_description: str = ""
    data_table: list[DataPoint] = []  # charts only
    latex: str | None = None  # equations only


class Issue(BaseModel):
    kind: str  # e.g. "wrong_number", "missing_label", "invented_detail", "too_long_for_decorative"
    detail: str


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
