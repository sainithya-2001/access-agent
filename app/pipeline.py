"""The whole pipeline for one document run, writing into the review store (used by the API
and scripts/run_review.py): ingest → detect → agent per figure → store."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

from app import pdf, store
from app.agent.orchestrator import FigureAgent
from app.detect import detect
from app.ingest import ingest
from app.llm import LOG_PATH
from app.models import FigureResult, Region

# Figures described at once. Time goes to waiting on model calls, so threads overlap the waits;
# the per-figure loop itself is unchanged and each figure's agent is independent.
WORKERS = 4


def describe_figures(figs: list[Region], pdf_path: str | Path, backend: str | None = None,
                     workers: int = WORKERS, tables: list[Region] | None = None) -> Iterator[tuple[Region, FigureResult]]:
    """Run the agent on each figure, `workers` at a time; yields (region, result) as each finishes.
    tables: the document's table regions (exact values when a chart matches one nearby).
    Workers only call models and render crops (under pdf.LOCK): the caller does any SQLite writes."""
    if workers <= 1:
        for r in figs:
            yield r, FigureAgent(r, str(pdf_path), backend, tables=tables).run()
        return
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="figure") as pool:
        jobs = {pool.submit(FigureAgent(r, str(pdf_path), backend, tables=tables).run): r for r in figs}
        for job in as_completed(jobs):
            yield jobs[job], job.result()


def log_offset() -> int:
    return sum(1 for _ in open(LOG_PATH, encoding="utf-8")) if Path(LOG_PATH).exists() else 0


def calls_since(offset: int) -> list[dict]:
    """This process's model calls logged after `offset` (other runs may share the log)."""
    if not Path(LOG_PATH).exists():
        return []
    lines = list(open(LOG_PATH, encoding="utf-8"))[offset:]
    return [c for c in map(json.loads, lines) if c.get("pid") == os.getpid()]


def run(con, key: str, pdf_path: str | Path, pages: str | None, page_check: bool = False,
        backend: str | None = None, echo: bool = False, workers: int = WORKERS) -> dict:
    """Process one run. Never raises: a failure is stored as status 'failed' with the error.
    Returns {"calls": n, "uncached": n} for the model calls made."""
    start = log_offset()
    try:
        store.set_progress(con, key, "processing", "Reading pages")
        with pdf.LOCK:  # a recrop job may be rendering from another PDF meanwhile (app/api.py)
            doc = ingest(pdf_path, pages)
        store.set_progress(con, key, "processing", f"Finding figures on {len(doc.pages)} pages")
        with pdf.LOCK:
            regions, _ = detect(doc, use_sam=page_check, use_check=page_check)
        store.save_layout(con, key, doc, regions)
        figs = [r for r in regions if r.kind == "figure"]
        store.set_progress(con, key, "processing", f"Describing {len(figs)} figures")
        for i, (r, res) in enumerate(describe_figures(figs, pdf_path, backend, workers,
                                                     [x for x in regions if x.kind == "table"]), 1):
            store.save_figure(con, key, res)
            store.set_progress(con, key, "processing", f"Described {i} of {len(figs)} figures")
            if echo:
                print(f"  {r.id:9} {r.caption or 'no caption':12} → {res.status.value}"
                      + (f" ({res.escalation_reason})" if res.escalation_reason else ""))
        store.set_progress(con, key, "ready", f"{len(figs)} figure{'' if len(figs) == 1 else 's'}")
    except Exception as e:  # keep the server alive; show the error in the UI
        store.set_progress(con, key, "failed", "", f"{e.__class__.__name__}: {e}")
        if echo:
            raise
    calls = calls_since(start)
    return {"calls": len(calls), "uncached": sum(1 for c in calls if not c.get("cached"))}
