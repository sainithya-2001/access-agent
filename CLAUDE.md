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
- **Limits enforced in code:** `MAX_ACTIONS = 8`, `MAX_REWRITES = 2`; approve refused unless the last verify passed; auto-verify after reread/rewrite/recrop; a rewrite over a disputed value (`wrong_number`) first re-reads that value at zoom (up to `REREADS_PER_REWRITE = 3`): the reread replaces the extracted value and is credited to the nearer reading within tolerance (a tie goes to the extraction), and if it agrees with neither reading → needs_review; any exception → needs_review (a run never crashes); a region with `review_reason` is never auto-approved.
- **Verifier:** `describe` and `extract_values` never see each other's output. `verify` = model check against the image + `code_checks` (data table vs extracted values, matched by label and by series for multi-series charts; box plots box by box from the extracted five numbers, plus whether a median line is drawn; numbers not in the figure or the page text; one row per label in single-series tables; stated counts ("eight bars", "three box plots") vs categories/series/boxes/marks; ratios ("twice as tall", ±15%) and two-box comparisons ("wider box", "longer left whisker"), not on no-scale charts, where guessed heights are too rough (replayed over dev runs 1–6: 8 false alarms in 15 flags, `eval/SCORING.md`); alt text ≤ 150; title/axis labels mentioned). Code issues force a fail (confidence capped at 0.6). `PASS_THRESHOLD = 0.8` (tools.py). Extract and verify run at temperature 0 (deterministic on the Meta API, not on Baseten).
- **General rules, straight to review:** color-only series (2+ series, no legend/labels; for two series the review banner adds which one peaks/ends higher, from the extracted values); no numeric scale on the value axis (`tools.no_numeric_scale`) → describe compares instead of estimating, any number not in the page text/labels → review "values can't be read: no numeric scale", and the export omits the data table; leaked checking-process words fail verify; an extraction with several values at one label in a single-series chart (e.g. one row per dot) → review. Describe may quote page-text values only as the text's, never as drawn positions.

## Code rules
1. **All model calls go through `app/llm.py` `chat()`** (SAM through `app/sam.py`); both log backend/model to `output/llm_calls.jsonl`.
2. **Cache every model response** (`app/cache.py`). Re-running a page must cost $0. Report uncached calls after runs. Detection results are cached per page too (`cache/detect/`, keyed by PDF hash, page, settings and a hash of the detection code).
3. **Validate every model output** with a Pydantic model (`app/models.py`). Invalid JSON → retry once, then escalate.
4. **Every agent decision appends a `TraceStep`**; the trace is shown in the review UI.
5. Develop on a few pages, not whole chapters. Prompts live in `app/agent/prompts/`, one file per task.
6. Frontend: plain HTML/CSS/JS in `web/`, served by FastAPI. Both the exported HTML and the review screen must pass **axe-core** with 0 violations.
7. Never invent chart numbers. Data tables come from `extract_values` after verification.
8. **Held-out set (`eval/heldout.json`: 8 Chapter 2 figures on PDF pages 168–175, 8 Chapter 12 figures on pages 630–670): run once, for the final report.** No prompt, threshold, or code changes may be based on held-out results. Do not run, inspect, or debug the pipeline on those pages during development; tune on `eval/benchmark.json` only.
9. Never print `.env`.
10. **Scorer frozen for the held-out run.** Once a held-out run starts, the scoring logic in `scripts/run_benchmark.py` and the judge prompt `app/agent/prompts/judge.md` do not change. Scoring and its post-hoc changes are documented in `eval/SCORING.md`.
11. **Figures run on a thread pool** (`pipeline.WORKERS = 4`, `--workers N`; the API runs recrop re-runs on its own pool). PyMuPDF outside the single-threaded ingest/detect holds `pdf.LOCK`; SQLite only through `store.connect()` (a locked connection); cache writes are atomic; the call log takes a thread lock and a file lock, and runs count their calls by pid.

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
| M7 | Review queue | ✅ SQLite store, FastAPI, queue UI; axe 0 violations + keyboard walk; figures described on a 4-worker pool, detection cached (pages 84–97 cached: 25.6 s → 7.0 s) |
| M8 | Benchmark | 🟡 dev set, 3 runs per backend before and after fixes A–G (`eval/results/dev_repeats.md`, `dev_repeats_r4-6.md`; scoring and hand checks in `eval/SCORING.md`): unsafe approvals Meta 3.7 (3–4) → 2.7 (2–3), Baseten 3.3 (3–4) → 4.7 (2–8), target 0; held-out run still to do |

## Add-ons (only after M8; decided later)
✅ **Blindfold mode** (`web/blindfold.js`, in the export and the review screen's Blindfold button): blacks out the screen and reads the document with the browser's speech synthesis, block by block. "Original" = what a screen reader gets from the untreated PDF (`assemble.original_reading`: text blocks in PDF order, no headings, images only "Image", table/chart text as one stream); "Accessible" = read from the exported page (headings, alt text, long descriptions, table cells with their headers). Off by default, modal, caption hidden from assistive tech; `check_a11y.py` runs it with silent speech.
✅ **Reading profiles** (`web/profiles.js`, export only): the student picks one in the bar at the top; remembered in localStorage (`reading-profile`), applied before first paint. CSS on `html[data-profile]`, presentation only (`check_a11y.py` compares main's accessibility tree across profiles). Screen reader (default, unchanged page) · Low vision (150% text, white on black, full-width figures, "Zoom figure" dialog 100–400%) · Reading support (Verdana, wider spacing, 36rem lines, read-aloud bar with the current block marked and the current word highlighted by the CSS Custom Highlight API) · Keyboard (4px focus outline + halo, 48px targets). Wide tables scroll in their own focusable `.table-wrap` so no profile scrolls sideways at 375px. Each profile, the zoom dialog and reading aloud: axe 0.
✅ **Hear the chart** (`app/sonify.py` decides, `web/sonify.js` plays; export and review detail panel): offered only for bar charts, histograms, line charts and frequency polygons that are approved (auto or staff) with `verification.passed`, not no-scale, not color-only, no boxes, every point valued, one point per label per series. Kind comes from the classification reason, alt text and the long description's first sentence (most specific first; any box/dot/pie/scatter/stem-and-leaf mention → no sound). Web Audio: series name spoken, then per point a soft click + a 260 ms triangle tone, 220–880 Hz log scale (bars/histograms from 0). Button toggles to Stop; Esc stops. `check_a11y.py` uses a fake AudioContext: tones and clicks = points, pitch order follows values, series names spoken; axe 0.
Still open: Lecture → verified notes · Accessible exam conversion · Muse Image diagram redraws.

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
python scripts/run_export.py --pages 84-91,95-97 [--page-check] [--workers N] # M6: output/<doc>/accessible.html
python scripts/check_a11y.py                                   # M6: axe-core on the newest export
python scripts/run_review.py --pages 84-97,109,124,130         # M7: pipeline into output/review.db
uvicorn app.api:app --port 8000                                # M7: review screen, http://localhost:8000
python scripts/check_a11y.py --review                          # M7: axe-core + keyboard walk on the UI
python scripts/reset_review_db.py output/<doc>/agent_results_meta.json --pages 84-97,109,124,130 [--db PATH]  # M7: rebuild the review DB from one results file, no staff edits (stop the server first; old DB kept as .bak-<time>)
python eval/make_benchmark.py                                  # M8: draft benchmark → eval/benchmark_draft.json
python scripts/run_benchmark.py --backend meta --set dev       # M8: run + score → eval/results/dev_meta.json
python scripts/run_benchmark.py --backend meta --set dev --rescore  # M8: re-score saved results, no model calls
python scripts/run_benchmark.py --backend meta --set dev --rep 2    # M8: repeat N: own cache namespace, crops, _rN results
python scripts/summarize_repeats.py --set dev --reps 3            # M8: mean and range over repeats → eval/results/dev_repeats.md
```
