# Access Agent

**An agent that makes course materials accessible for students with print disabilities, and checks its own work before a person has to.**

Live demo: https://access-agent-y65u.onrender.com · Demo video: [Watch on YouTube](https://youtu.be/w8TodRx_pPI)

Built solo for the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026), track: *Build for Your World*. Everything in this repository was written during the hackathon (see the commit history).

---

## The problem

Students who are blind, have low vision, or have other print disabilities get their course materials through campus disability services offices. Plain text converts easily. Figures, charts, tables, equations and scanned pages do not: each needs a description written by someone who understands what it teaches. Offices publish turnaround times of days to weeks for alternative-format materials, and the visual content is the slow part.

I worked at a university disability services office during my master's program. AI can already write alt text, but staff can't trust it without checking every description, which removes most of the time saved. Note-takers hired for students who need lecture notes are often late, and staff spend time chasing them.

**Access Agent's answer:** describe every figure, **verify each description against the figure independently**, and send only the uncertain ones to a person, with a plain-language reason.

## What it does

**For disability services staff (review screen)**
- Upload a course PDF (scanned or digital). The agent finds every figure and table, describes it, and checks the description.
- Figures it can verify are auto-approved. The rest go to a review queue with a plain reason and the next step ("The chart's two lines are told apart only by color. Find which is which in the nearby table and name them in the description.").
- Staff edit, approve or reject; adjust a bad crop and the agent re-runs on it; every agent decision is shown as a readable trace.
- **Blindfold mode:** hear a page the way a screen reader user would, before (the untreated PDF) and after (Access Agent's output).

**For students (accessible output)**
- Accessible HTML: real headings and reading order, alt text and long descriptions, data tables for charts, proper table headers, MathML for equations, expanded stem-and-leaf plots.
- **Reading profiles:** screen reader, low vision, reading support (read-aloud with highlighting), keyboard. Same content, different presentation.
- **Hear the chart:** charts whose values passed verification play as tones, pitch following the values.
- **Lecture notes:** a recorded lecture becomes timestamped notes, with each point checked against the transcript, captions (.vtt), and verified descriptions of the slides inserted where the instructor says "as you can see here." A student inbox shows "New materials ready" when a lecture is processed.

## How it works

```
PDF ──► ingest (PyMuPDF) ──► detect figures & tables
                               │  embedded images, vector drawings, text tables (free)
                               │  Muse Spark page check + SAM 3.1 box refinement
                               ▼
        per figure: classify ─► read values ─► describe ─► VERIFY ─┐
                                   (independent)                   │
             ┌──── agent loop (Muse Spark): reread value at zoom, rewrite, recrop,
             │     mark decorative, escalate ── max 8 actions, 2 rewrites
             ▼
        auto-approve  or  human review queue ──► accessible HTML
```

**Verification is the core.** The describer and the value reader never see each other's output, so their errors don't agree. The verifier compares both against the image, and free code checks catch what a model judge misses:
- data-table values vs. the independent reading (series-aware, tolerance by whether values are printed or read off an axis)
- numbers in the description that appear nowhere in the figure or the page text
- axis titles and labels mentioned; stated counts ("8 bars") matching what's drawn
- comparisons ("twice as tall", "wider box") checked against the values
- leaked process wording ("the verifier said…") never reaching a student

**Rules that send figures to a person instead of guessing:** values on an axis with no numbers; series told apart only by color with no legend; a figure box the detectors couldn't confirm; three readings that disagree.

**When the page has the exact data,** e.g. the table a chart was drawn from, the description uses the table's values and says so ("Values from Table 2.9"), but only if every value matches the reading.

### Meta models

| Model | Role |
|---|---|
| **Muse Spark** (Meta Model API) | classify, read values, describe, verify, agent decisions, page check, lecture notes |
| **SAM 3.1** (Meta Model API) | refines figure boxes found by the page check; scanned pages |
| **Muse Voice Transcribe** (Meta Model API) | lecture transcription with timestamps |
| **Muse Wonder** | independent judge in the benchmark only |
| **Muse Spark Open** (open weights, via Baseten) | the same pipeline, self-hostable, so course materials and accommodation data can stay on campus. Measured separately below. |

## Results

**Benchmark.** 19 development figures from OpenStax *Introductory Statistics 2e*, Chapter 2: bar graphs, histograms, frequency polygons, time series, box plots, dot plots, a calculator screenshot, a photo, blank axes and a stem-and-leaf plot. Reference answers were drafted from the textbook's own data tables, then checked item by item against each figure image; corrections and places where the drawing differs from the book's numbers are recorded. All numbers below come from code frozen at tag `freeze-v1`, 3 runs per backend with separate caches. Full details: [`eval/results/FINAL.md`](eval/results/FINAL.md).

| Development set, mean (min–max) over 3 runs | Muse Spark (Meta API) | Muse Spark Open (Baseten) |
|---|---|---|
| **Real errors auto-approved, per run of 19 figures** | **0.67 (0–1)** | **1.33 (0–2)** |
| All unsafe approvals, including the two known cases below | 2.33 (2–3) | 2.33 (1–3) |
| Correct outcome (of 19) | 17.0 (16–18) | 16.3 (16–17) |
| Values correct (of 162) | 146.3 (144–148), about 90% | 145.0 (141–148) |
| Sent to a person (of 19) | 3.0 (2–4), about 16% | 4.3 (3–6), about 23% |
| Cost per figure (including the benchmark judge) | $0.135 | $0.169 |

The two known cases: Figure 2.14 is drawn with a quartile at about 53 where the book's data give 56, so a description that follows the drawing is scored wrong; Figure 2.59's descriptions call seven states and Washington, DC "eight states". Any other error on those figures is counted as real.

**Held-out set.** 13 figures never used during development: 5 from Chapter 2 and 8 from Chapter 12, including scatter plots and regression lines, chart types absent from the development set. Reference answers were written from the figures and the textbook text without seeing any agent output, and **committed before the held-out run** (commit d8eaf76). Run once per backend on frozen code.

| Held-out set, one run | Muse Spark (Meta API) | Muse Spark Open (Baseten) |
|---|---|---|
| Auto-approved / sent to a person (of 13) | 7 / 6 | 5 / 8 |
| Auto-approved with a real error | 2 (12.6, 12.32) | 1 (12.6) |
| Values correct, on the 6 figures with recorded values (of 38) | 35 | 34 |
| Cost per figure (agent only) | $0.066 | $0.092 |

On unfamiliar chart types the agent sent more figures to a person (6 of 8 Chapter 12 figures on Muse Spark), which is the behavior we want. The frozen scorer also flagged Figure 2.63 on Muse Spark; that is a scorer false alarm (its description correctly says 10 bars for 11 classes, one empty) and is not counted above.

**Verifier test:** with one known error planted per description (wrong number, reversed trend, missing label, invented detail, wrong label), it caught 5 of 5 with 0 false alarms.

**Accessibility:** 0 axe-core violations across every state of the review screen, the exported HTML (all four reading profiles) and the lecture notes page; keyboard-only walkthroughs pass.

**Cost and speed:** about 13 cents per figure on Muse Spark, including the benchmark judge. The 2.5-minute demo lecture (148 s) cost $0.14 in new calls: transcription $0.007, notes $0.03, and $0.10 to check its 32 note points one by one; its two slide figures were already described (about $0.07 each when new). Figures are processed 4 at a time.

### What we learned building the evaluation
- **The self-correction loop could make correct answers wrong.** When the describer and value reader disagreed, the agent sided with the reader and rewrote, turning correct descriptions into wrong ones (5 cases in 12 diagnostic runs) that were then approved. Fix: a zoomed re-read breaks the tie before any rewrite.
- **Single runs mislead.** The same code varied by several unsafe approvals between runs, so every development number above is a mean over repeated runs with separate caches. Muse Spark Open made more real errors and sent more figures to review than Muse Spark, even at temperature 0.
- **Some checks we tried didn't work, so we didn't ship them.** Checking ratio claims on charts with no numeric scale flagged correct descriptions about as often as wrong ones; those figures keep the stricter "any unsourced number → review" rule instead.
- **A model judge without an answer key is too lenient.** On the held-out set it found no errors in Muse Spark's descriptions; scoring against the checked references found two.
- **The scorer was changed only for documented bugs**, in both directions, before the held-out run, then frozen. See [`eval/SCORING.md`](eval/SCORING.md).

## How this differs from existing tools

- **ALADDIN** (Georgia Tech, ACM ASSETS 2025) generates context-aware alt text for Word and PowerPoint files; review is all-or-nothing and no accuracy figures are reported. Access Agent adds PDFs including scanned ones, full document structure, an independent verifier with confidence-based triage, lecture notes, and a published, repeatable benchmark. No code from ALADDIN was used.
- Commercial alt-text services (e.g. Continual Engine's Invicta) are closed and publish no evaluation.

## Limitations

- **Not yet tested with blind or low-vision students.** That is the most important next step.
- Some chart readings remain unreliable on figures drawn imprecisely or without numeric scales. On the development set about 0.7 real errors per run of 19 figures still get auto-approved; on the held-out set, 2 of the 7 figures Muse Spark approved had errors, and the "no numeric scale" rule missed scatter plots without axis numbers (Figure 12.6).
- The held-out set is small (13 figures), and every number comes from one textbook.
- Lecture capture integrations (Canvas, Panopto, Kaltura) are simulated by a watched folder; real integrations need university IT approval. Recording classes depends on university policy and consent.
- English only; no braille, EPUB or Word output yet.
- Free-tier API credits may allow content to be used for product improvement; real deployments should use paid credits or the self-hosted open-weights model, and never send real student materials through free credits.

## Run it

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows; source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env                                # add MODEL_API_KEY (and BASETEN_API_KEY for open weights)
python scripts/test_api.py                          # check the API
python -m uvicorn app.api:app --port 8000           # review screen at http://localhost:8000
python scripts/run_benchmark.py --backend meta --set dev
```
Deployment: see [`DEPLOY.md`](DEPLOY.md). The live demo runs in `DEMO_MODE` (precomputed documents, no model calls; uploads need a passcode).

## Data and licenses

Demo and benchmark materials: OpenStax *Introductory Statistics 2e* (CC BY 4.0). The demo lecture: synthetic narration (Kokoro TTS), using OpenStax slides (CC BY 4.0). Code: MIT (see LICENSE).

## Built with

Meta Model API and Muse Spark Open on Baseten for everything the app does. The code was written with help from Claude Code (Anthropic); design decisions, the benchmark answer key checks and the evaluation choices are documented in the commit history, `CLAUDE.md` and `eval/SCORING.md`.
