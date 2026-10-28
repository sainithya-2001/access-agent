"""The whole pipeline for one document run, writing into the review store (used by the API
and scripts/run_review.py): ingest → detect → agent per figure → store."""

from __future__ import annotations

import json
from pathlib import Path

from app import store
from app.agent.orchestrator import FigureAgent
from app.detect import detect
from app.ingest import ingest
from app.llm import LOG_PATH


def _log_lines() -> int:
    return sum(1 for _ in open(LOG_PATH, encoding="utf-8")) if Path(LOG_PATH).exists() else 0


def run(con, key: str, pdf_path: str | Path, pages: str | None, page_check: bool = False,
        backend: str | None = None, echo: bool = False) -> dict:
    """Process one run. Never raises: a failure is stored as status 'failed' with the error.
    Returns {"calls": n, "uncached": n} for the model calls made."""
    start = _log_lines()
    try:
        store.set_progress(con, key, "processing", "Reading pages")
        doc = ingest(pdf_path, pages)
        store.set_progress(con, key, "processing", f"Finding figures on {len(doc.pages)} pages")
        regions, _ = detect(doc, use_sam=page_check, use_check=page_check)
        store.save_layout(con, key, doc, regions)
        figs = [r for r in regions if r.kind == "figure"]
        for i, r in enumerate(figs, 1):
            store.set_progress(con, key, "processing", f"Describing figure {i} of {len(figs)}")
            res = FigureAgent(r, str(pdf_path), backend).run()
            store.save_figure(con, key, res)
            if echo:
                print(f"  {r.id:9} {r.caption or 'no caption':12} → {res.status.value}"
                      + (f" ({res.escalation_reason})" if res.escalation_reason else ""))
        store.set_progress(con, key, "ready", f"{len(figs)} figure{'' if len(figs) == 1 else 's'}")
    except Exception as e:  # keep the server alive; show the error in the UI
        store.set_progress(con, key, "failed", "", f"{e.__class__.__name__}: {e}")
        if echo:
            raise
    calls = [json.loads(line) for line in open(LOG_PATH, encoding="utf-8")][start:] if Path(LOG_PATH).exists() else []
    return {"calls": len(calls), "uncached": sum(1 for c in calls if not c.get("cached"))}
