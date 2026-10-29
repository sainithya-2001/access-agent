# Access Agent: context for Claude Code

## What this is
An agent that turns course materials (scanned or digital PDFs) into accessible HTML for **disability services staff** at universities. Its distinguishing feature: it **verifies its own figure descriptions** and sends only low-confidence items to a human reviewer.

Built solo for the **Meta Global AI Developer Hackathon** (Cerebral Valley), Oct 27 – Nov 4, 2026. **Submission deadline: Wed Nov 4, 9:00 PM ET** (target: submit by 5 PM).

Pitch: "AI can already write alt text, but staff can't trust it without checking everything. This agent checks its own descriptions and sends only the uncertain ones to a person."

## Hackathon rules that affect code
- **New work only.** Everything here is written during the event. Do not copy code from ALADDIN or other projects.
- **Must use Meta models.** Two backends, both OpenAI-compatible, chosen with `LLM_BACKEND` in .env or `--backend`:
  - `meta`: Meta Model API (`https://api.meta.ai/v1`), Muse Spark + SAM 3.1. **$150 in credits total.**
  - `baseten`: **Muse Spark Open** (open weights, `meta-models/muse-spark-open-1026`) on Baseten (**$100 in credits**). Same agent loop; every call's backend is logged as evidence for the open-weights prize.
- **Stay clear of banned project types:** no Streamlit, no chat box, no dashboard as the main feature. The UI is a work queue.
- Repo must be **public**. Demo material is openly licensed (OpenStax *Introductory Statistics 2e*, CC BY).

## Pipeline
```
PDF → ingest.py   page images, text blocks (heading lines split off), embedded images, scanned flag
    → detect.py   free layers first: embedded images, text tables (cells read from word positions),
                  vector charts; Table-captioned figures → tables; stemplots expanded (stemplot.py).
                  Optional page check (--page-check): Muse Spark lists missed figures → SAM 3.1 locates them
    → agent       per figure (orchestrator.py), see below
    → assemble.py accessible HTML        → store.py / api.py / web/   staff review queue
```
Code controls the order; the model decides **per figure** inside a capped loop.

### Meta models
| Model | Role |
|---|---|
| Muse Spark (`SPARK_MODEL`) / Muse Spark Open (`BASETEN_MODEL`) | classify, extract values, describe, verify, agent decisions, page check |
| SAM 3.1 (`sam-3.1`, Responses API, app/sam.py) | **In use**: concept prompt = the page check's noun phrase; prefers the match next to the right caption, else nearest Spark's 0–1000 box; no match → Spark's box + `review_reason` (never auto-approved). Box widening: text labels on digital pages, union with Spark's box on scanned pages. |
| Muse Voice Transcribe | only for the optional lecture add-on |

## Agent (app/agent/)
- **First pass, by code:** classify → extract_values → (labels cut off → recrop 30pt, re-extract) → describe → verify. Pass → auto-approved, no agent call.
- **On fail**, Muse Spark picks JSON actions with a one-line `why`: reread_value, rewrite, recrop, verify, mark_decorative, approve, escalate.
- **Limits enforced in code:** `MAX_ACTIONS = 8`, `MAX_REWRITES = 2`; approve refused unless the last verify passed; auto-verify after reread/rewrite/recrop; any exception → needs_review (a run never crashes); a region with `review_reason` is never auto-approved.
- **Verifier:** `describe` and `extract_values` never see each other's output. `verify` = model check against the image + `code_checks` (data table vs extracted values, matched by label and by series for multi-series charts; numbers not in the figure; alt text ≤ 150; title/axis labels mentioned). Code issues force a fail (confidence capped at 0.6). `PASS_THRESHOLD = 0.8` (tools.py).

## Code rules
1. **All model calls go through `app/llm.py` `chat()`** (SAM through `app/sam.py`); both log backend/model to `output/llm_calls.jsonl`.
2. **Cache every model response** (`app/cache.py`). Re-running a page must cost $0. Report uncached calls after runs.
3. **Validate every model output** with a Pydantic model (`app/models.py`). Invalid JSON → retry once, then escalate.
4. **Every agent decision appends a `TraceStep`**; the trace is shown in the review UI.
5. Develop on a few pages, not whole chapters. Prompts live in `app/agent/prompts/`, one file per task.
6. Frontend: plain HTML/CSS/JS in `web/`, served by FastAPI. Both the exported HTML and the review screen must pass **axe-core** with 0 violations.
7. Never invent chart numbers. Data tables come from `extract_values` after verification.
8. **Held-out set (`eval/heldout.json`: 8 Chapter 2 figures on PDF pages 168–175, 8 Chapter 12 figures on pages 630–670): run once, for the final report.** No prompt, threshold, or code changes may be based on held-out results. Do not run, inspect, or debug the pipeline on those pages during development; tune on `eval/benchmark.json` only.
9. Never print `.env`.
10. **Scorer frozen for the held-out run.** Once a held-out run starts, the scoring logic in `scripts/run_benchmark.py` and the judge prompt `app/agent/prompts/judge.md` do not change. Scoring and its post-hoc changes are documented in `eval/SCORING.md`.

## Milestones
| # | Milestone | Status |
|---|---|---|
| M0 | API check | ✅ Muse Spark and SAM 3.1 confirmed; Muse Spark Open on Baseten passes the text + vision test (agent not yet run on it) |
| M1 | Ingest | ✅ |
| M2 | Figure detection | ✅ free layers + page check; page check finds 9/9 test figures (mean IoU 0.65; 0.85 with union widening) |
| M3 | Classify + describe | ✅ |
| M4 | Extract + verify | ✅ 5/5 planted errors caught, 0 false alarms (`test_verifier.py`) |
| M5 | Agent loop | ✅ |
| M6 | HTML export | ✅ axe-core 0 violations; manual screen-reader pass still to do |
| M7 | Review queue | ✅ SQLite store, FastAPI, queue UI; axe 0 violations + keyboard walk |
| M8 | Benchmark | 🟡 dev set done on both backends (`eval/results/`, scoring in `eval/SCORING.md`): unsafe approvals Meta 2, Baseten 3 (target 0); held-out run still to do |

## Add-ons (only after M8; decided later)
Blindfold mode (browser TTS) · Reading profiles · Hear the chart (sonification of verified values) · Lecture → verified notes · Accessible exam conversion · Muse Image diagram redraws.

## Related work (cite in README, do not copy)
- ALADDIN (Georgia Tech, ACM ASSETS 2025): context-aware alt text for Word/PowerPoint with Gemini; all-or-nothing review; no accuracy numbers.
- Imperial College AI Alt Text Generator: single-image proof of concept.
- This project adds: scanned PDFs, document structure, an automated verifier, confidence triage, a published benchmark.

## Commands
Add `--backend baseten` to any model-calling script to run on Muse Spark Open.
```bash
pip install -r requirements.txt && npm install && python -m playwright install chromium
python scripts/test_api.py [--backend baseten]                 # M0
python scripts/run_ingest.py --pages 95-97                     # M1 (PDF in data/samples)
python scripts/run_detect.py --pages 95-97 --free-only         # M2, no API calls
python scripts/run_detect.py --pages 85,89,96 --test-check     # M2: page-check localization score
python scripts/run_figures.py --pages 96                       # M3
python scripts/test_verifier.py                                # M4: planted-error test
python scripts/run_agent.py --pages 96 [--inject wrong_number] # M5: agent loop with traces
python scripts/run_export.py --pages 84-91,95-97 [--page-check] # M6: output/<doc>/accessible.html
python scripts/check_a11y.py                                   # M6: axe-core on the newest export
python scripts/run_review.py --pages 84-97,109,124,130         # M7: pipeline into output/review.db
uvicorn app.api:app --port 8000                                # M7: review screen, http://localhost:8000
python scripts/check_a11y.py --review                          # M7: axe-core + keyboard walk on the UI
python eval/make_benchmark.py                                  # M8: draft benchmark → eval/benchmark_draft.json
python scripts/run_benchmark.py --backend meta --set dev       # M8: run + score → eval/results/dev_meta.json
python scripts/run_benchmark.py --backend meta --set dev --rescore  # M8: re-score saved results, no model calls
```
