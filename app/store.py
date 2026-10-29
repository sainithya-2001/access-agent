"""SQLite store for the review screen (M7): documents, figure results, and human edits.

A "document" here is one processing run: a PDF plus a page range. Figure results are stored as
the FigureResult JSON the agent produced; human decisions are appended to `edits` (who, when,
what) and the figure's current status/description are updated, so the agent's original output
is always recoverable from the first trace.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import Document, FigureResult, Region, Status

DB_PATH = Path("output/review.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,           -- <pdf hash>-<page spec>
  pdf_id TEXT NOT NULL,          -- ingest's document id (output/<pdf_id>/...)
  source_path TEXT NOT NULL,
  name TEXT NOT NULL,            -- file name shown in the UI
  pages TEXT NOT NULL,           -- page spec, e.g. "84-97,109"
  status TEXT NOT NULL,          -- queued | processing | ready | failed
  progress TEXT NOT NULL DEFAULT '',
  error TEXT,
  document_json TEXT,            -- ingest output (Document)
  regions_json TEXT,             -- every detected region, figures and tables
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS figures (
  id TEXT PRIMARY KEY,           -- <document id>:<region id>
  document_id TEXT NOT NULL REFERENCES documents(id),
  region_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  y0 REAL NOT NULL,              -- reading order within the page
  status TEXT NOT NULL,
  confidence REAL,
  result_json TEXT NOT NULL,     -- FigureResult, including human edits to the description
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  figure_id TEXT NOT NULL REFERENCES figures(id),
  action TEXT NOT NULL,          -- save | approve | edit | reject | recrop
  who TEXT NOT NULL,
  at TEXT NOT NULL,
  alt_text TEXT,
  long_description TEXT,
  note TEXT,
  previous_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS figures_doc ON figures(document_id, page, y0);
CREATE INDEX IF NOT EXISTS edits_fig ON edits(figure_id, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = path or DB_PATH  # read at call time, so tests can point DB_PATH elsewhere
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def document_key(pdf_id: str, pages: str | None) -> str:
    spec = (pages or "all").replace(" ", "")
    return f"{pdf_id}-{spec}"


# ---------- documents ----------

def create_document(con, key: str, pdf_id: str, source_path: str, name: str, pages: str | None) -> None:
    t = now()
    with con:
        con.execute("DELETE FROM edits WHERE figure_id IN (SELECT id FROM figures WHERE document_id = ?)", (key,))
        con.execute("DELETE FROM figures WHERE document_id = ?", (key,))
        con.execute("""INSERT OR REPLACE INTO documents
            (id, pdf_id, source_path, name, pages, status, progress, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', '', NULL, ?, ?)""",
                    (key, pdf_id, source_path, name, pages or "all", t, t))


def set_progress(con, key: str, status: str, progress: str = "", error: str | None = None) -> None:
    with con:
        con.execute("UPDATE documents SET status = ?, progress = ?, error = ?, updated_at = ? WHERE id = ?",
                    (status, progress, error, now(), key))


def save_layout(con, key: str, doc: Document, regions: list[Region]) -> None:
    with con:
        con.execute("UPDATE documents SET document_json = ?, regions_json = ?, updated_at = ? WHERE id = ?",
                    (doc.model_dump_json(), json.dumps([r.model_dump(mode="json") for r in regions]),
                     now(), key))


def list_documents(con) -> list[dict]:
    rows = con.execute("""SELECT d.id, d.name, d.pages, d.status, d.progress, d.error, d.updated_at,
        SUM(f.status = 'needs_review') AS needs_review, SUM(f.status = 'auto_approved') AS auto_approved,
        COUNT(f.id) AS figures
        FROM documents d LEFT JOIN figures f ON f.document_id = d.id
        GROUP BY d.id ORDER BY d.updated_at DESC""").fetchall()
    return [dict(r) for r in rows]


def get_document(con, key: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM documents WHERE id = ?", (key,)).fetchone()


def load_layout(con, key: str) -> tuple[Document, list[Region]]:
    row = get_document(con, key)
    doc = Document.model_validate_json(row["document_json"])
    regions = [Region.model_validate(r) for r in json.loads(row["regions_json"])]
    return doc, regions


# ---------- figures ----------

def save_figure(con, key: str, res: FigureResult) -> None:
    conf = res.verification.confidence if res.verification else None
    with con:
        con.execute("""INSERT OR REPLACE INTO figures
            (id, document_id, region_id, page, y0, status, confidence, result_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"{key}:{res.region.id}", key, res.region.id, res.region.page, res.region.bbox.y0,
                     res.status.value, conf, res.model_dump_json(), now()))


def figures(con, key: str) -> list[tuple[str, FigureResult]]:
    rows = con.execute("SELECT id, result_json FROM figures WHERE document_id = ? ORDER BY page, y0",
                       (key,)).fetchall()
    return [(r["id"], FigureResult.model_validate_json(r["result_json"])) for r in rows]


def get_figure(con, fid: str) -> FigureResult | None:
    row = con.execute("SELECT result_json FROM figures WHERE id = ?", (fid,)).fetchone()
    return FigureResult.model_validate_json(row["result_json"]) if row else None


def edits_for(con, fid: str) -> list[dict]:
    rows = con.execute("SELECT action, who, at, alt_text, long_description, note, previous_status "
                       "FROM edits WHERE figure_id = ? ORDER BY id", (fid,)).fetchall()
    return [dict(r) for r in rows]


def review(con, fid: str, action: str, who: str, alt_text: str | None = None,
           long_description: str | None = None, note: str | None = None) -> FigureResult:
    """Record a human decision. save replaces the text and keeps the status (a draft); approve keeps
    the text, edit replaces it and approves, reject flags it."""
    res = get_figure(con, fid)
    if res is None:
        raise KeyError(fid)
    previous = res.status.value
    if action in ("edit", "save"):
        if res.description is None:
            from app.models import Description
            res.description = Description(alt_text="")
        res.description.alt_text = alt_text if alt_text is not None else res.description.alt_text
        if long_description is not None:
            res.description.long_description = long_description
        if action == "edit":
            res.status = Status.edited
    elif action == "approve":
        res.status = Status.approved
    elif action == "reject":
        res.status = Status.rejected
    else:
        raise ValueError(action)
    with con:
        con.execute("""INSERT INTO edits (figure_id, action, who, at, alt_text, long_description, note,
            previous_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fid, action, who, now(), alt_text if action in ("edit", "save") else None,
                     long_description if action in ("edit", "save") else None, note, previous))
        con.execute("UPDATE figures SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                    (res.status.value, res.model_dump_json(), now(), fid))
    return res


def start_recrop(con, fid: str, who: str, region: Region) -> FigureResult:
    """Staff drew a new box: store the new region (so its crop is served at once), record it, and
    mark the figure pending until the agent has run again."""
    res = get_figure(con, fid)
    if res is None:
        raise KeyError(fid)
    previous = res.status.value
    res.region = region
    res.status = Status.pending
    res.escalation_reason = None
    bbox = region.bbox
    note = f"New crop: x {bbox.x0:.0f}–{bbox.x1:.0f}, y {bbox.y0:.0f}–{bbox.y1:.0f} (PDF points)"
    with con:
        con.execute("""INSERT INTO edits (figure_id, action, who, at, note, previous_status)
            VALUES (?, 'recrop', ?, ?, ?, ?)""", (fid, who, now(), note, previous))
        con.execute("UPDATE figures SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                    (res.status.value, res.model_dump_json(), now(), fid))
    return res
