# Access Agent: context for Claude Code

## What this is
An agent that turns course materials (scanned or digital PDFs) into accessible HTML for **disability services staff** at universities. Its distinguishing feature: it **verifies its own figure descriptions** and sends only low-confidence items to a human reviewer.

Built solo for the **Meta Global AI Developer Hackathon** (Cerebral Valley). Building period Oct 27 – Nov 4, 2026. **Submission deadline: Wed Nov 4, 9:00 PM ET** (target: submit by 5 PM).

Pitch: "AI can already write alt text, but staff can't trust it without checking everything. This agent checks its own descriptions and sends only the uncertain ones to a person."

## Hackathon rules that affect code
- **New work only.** Everything in this repo must be written during the event. Do not copy code from ALADDIN or other projects.
- **Must use Meta models** via the Meta Model API (base URL `https://api.meta.ai/v1`, OpenAI-compatible). Budget: **$150 in credits total**.
- **Banned project types to stay clear of:** Streamlit apps, dashboards as the main feature, "AI for Education" chatbots, simple image analyzers, basic RAG. So: **no Streamlit**, no chat box, the UI is a work queue.
- Repo must be **public**. Demo materials must be openly licensed (OpenStax textbooks, CC BY).

## Architecture
```
PDF upload
 → ingest.py: page images, text blocks, embedded images; flag scanned pages
 → detect.py: figure regions (SAM 3.1 if available in the API, else Muse Spark bounding boxes;
              for digital PDFs, PyMuPDF embedded images first)
 → agent/orchestrator.py, per figure, a bounded tool-calling loop (max ~6 steps):
      classify → describe → extract_values → verify → (recrop | rewrite | mark_decorative | escalate)
 → triage: confidence ≥ threshold → auto-approved; else → human review queue
 → assemble.py: semantic HTML (headings, reading order, alt text, long descriptions,
                data tables for charts, MathML for equations)
```
Code controls the overall order (reliable, cheap). The model decides **per figure** inside the loop (real autonomy, capped).

### Meta models
| Model | Role |
|---|---|
| Muse Spark (`SPARK_MODEL` in .env) | classify, describe, extract values, verify, orchestrate |
| SAM 3.1 (`sam-3.1`, Responses API, see app/sam.py) | scanned-page figure detection; locates figures the Muse Spark page check reports (its noun phrase as a concept prompt; match next to the right caption, else nearest Spark's 0–1000 box; no match → Spark's box + human review) |
| Muse Voice Transcribe | only for the optional lecture add-on |

### Agent tools (app/agent/tools.py)
- `classify_region`: type ∈ {chart, diagram, photo, equation, table_image, text_image, decorative, other}
- `describe`: short alt text (≤ ~150 chars) + long description; data table for charts; LaTeX for equations
- `extract_values`: reads numbers/labels from the image **independently** of the description
- `verify`: compares description vs extracted values vs image; returns issues + confidence 0–1
- `recrop`: widen the crop when labels/axes/legend are cut off
- `mark_decorative`: alt="" instead of a description
- `escalate`: send to the review queue with a reason

Decision rules: mismatch → rewrite (max 2 retries); cut-off labels → recrop; decorative image getting a long description → mark_decorative; still failing or confidence < threshold → escalate.

## Verifier design
- `describe` and `extract_values` never see each other's output (independent readings; otherwise errors agree).
- `verify` = model check against the image + free deterministic checks in code (`code_checks`: data table vs extracted values, numbers not in the figure, alt text length, title/axis labels mentioned in the long description). Code issues force a fail.
- PASS_THRESHOLD = 0.8 in app/agent/tools.py.

## Agent loop (app/agent/orchestrator.py)
- First pass by code: classify → extract → describe → verify. Pass → auto-approved, no agent call.
- Fail → Muse Spark picks JSON actions (reread_value, rewrite, recrop, verify, mark_decorative, approve, escalate), each with a one-line `why`.
- Code enforces: approve refused unless last verify passed; auto-verify after reread/rewrite/recrop; MAX_ACTIONS=8, MAX_REWRITES=2; any exception → needs_review (run never crashes).

## Code rules
1. **All model calls go through `app/llm.py` `chat()`**, which logs backend/model/request id to `output/llm_calls.jsonl`.
2. **Cache every model response** with `app/cache.py` (key = model + prompt + image hash). Re-running a page must cost $0.
3. **Validate every model output** against a Pydantic model in `app/models.py`. On invalid JSON, retry once, then escalate. Never trust free text.
4. **Every agent decision appends a `TraceStep`** (human-readable). The trace is shown in the UI and is central to the demo.
5. Develop on **2–3 pages**, not whole chapters, to save credits.
6. Prompts live in `app/agent/prompts/`, one file per task, so they can be tuned without code changes.
7. Frontend: plain HTML/CSS/JS in `web/`, served by FastAPI. The exported HTML itself must pass **axe-core** checks.
8. Never invent chart numbers. Charts' data tables come from `extract_values` after verification.

## Milestones (base). Build in order.
| # | Milestone | Done when |
|---|---|---|
| M0 | API check | `scripts/test_api.py` passes; model IDs confirmed; SAM 3.1 availability known |
| M1 | Ingest | One OpenStax PDF → page images, text blocks, embedded images; scanned pages flagged |
| M2 | Figure detection | Every figure on 3 test pages has a correct box, drawn as an overlay |
| M3 | Classify + describe | Each figure has a type + description as validated JSON |
| M4 | Extract + verify | Verifier catches ≥ 4 of 5 **planted errors** (most important milestone) |
| M5 | Agent loop | Retries, recrop, decorative, escalation work; readable trace per figure |
| M6 | HTML export | Passes axe-core; reads correctly in a screen reader |
| M7 | Review queue | Low-confidence figures with trace + confidence; approve/edit persist in SQLite |
| M8 | Benchmark | Accuracy, verifier catch rate, review effort saved, cost per page recorded |

## Add-ons (only after M8; decided later)
Blindfold mode (hear the page before/after via browser TTS) · Reading profiles (screen reader, low vision, reading support, keyboard) · Hear the chart (Web Audio sonification of verified values) · Lecture → verified notes + auto-trigger + student inbox · Accessible exam / practice material conversion · Muse Image diagram redraws.

## Related work (cite in README, do not copy)
- ALADDIN (Georgia Tech, ACM ASSETS 2025): context-aware alt text for Word/PowerPoint with Gemini; review is all-or-nothing; no accuracy numbers reported.
- Imperial College AI Alt Text Generator: single-image proof of concept.
- This project adds: scanned PDFs, document structure, an automated verifier, confidence triage, a published benchmark.

## Commands
```bash
pip install -r requirements.txt
python scripts/test_api.py                 # M0
python scripts/run_ingest.py --pages 95-97          # M1 (uses the PDF in data/samples)
python scripts/run_detect.py --pages 95-97 --free-only   # M2, no API calls
python scripts/run_detect.py --pages 95-97          # M2 with SAM 3.1 + Muse Spark page check
python scripts/run_figures.py --pages 96            # M3: classify, extract, describe, verify
python scripts/test_verifier.py                     # M4: planted-error test of the verifier
python scripts/run_agent.py --pages 96              # M5: agent loop with traces
python scripts/run_agent.py --pages 96 --inject wrong_number   # M5 demo: agent fixes a planted error
python scripts/run_export.py --pages 84-91,95-97   # M6: accessible.html (agent on every figure, cached)
python scripts/check_a11y.py                       # M6: axe-core on the newest accessible.html (npm install first)
python scripts/run_review.py --pages 84-97,109,124,130   # M7: pipeline into the review store (SQLite)
uvicorn app.api:app --port 8000                    # M7: review screen at http://localhost:8000
python scripts/check_a11y.py --review              # M7: axe-core + keyboard walk on the review screen
```
