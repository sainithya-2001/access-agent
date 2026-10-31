# Final benchmark (code frozen at tag `freeze-v1`)

Code: tag `freeze-v1` (commit a926fc4). Since then only these were added, none of which changes the
agent, prompts, detection or the scorer: `scripts/judge_heldout.py` (reference-free judge pass),
`eval/make_heldout_refs.py` + `eval/heldout_refs.json` (held-out references, committed before the
held-out run), `scripts/score_heldout_refs.py` (scores saved held-out results with the frozen scorer).

Prices: list prices in `scripts/run_benchmark.py` `PRICES` (Muse Spark and Muse Spark Open $1.25 / $4.25
per million input / output tokens, Muse Wonder judge $4 / $20); not checked against the providers'
billing. The judge always runs on the Meta API, also for Baseten runs.

## Dev set: reps 7–9 per backend (19 figures)

Fresh cache namespace per rep (`rep7`–`rep9`; 13 entries an Oct 29 test had left in `rep9` were moved
to `cache/aside_rep9_2026-10-29/` before the runs). Each rep has its own crop folder. Meta and Baseten
ran in parallel, reps one after another. Full table: `dev_repeats_r7-9.md`; per-run results:
`dev_<backend>_r7.json` … `_r9.json`.

| Metric, mean (min–max) | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Unsafe approvals (target 0) | 2.33 (2–3) | 2.33 (1–3) |
|   caught by code checks | 1 (all runs) | 0.67 (0–1) |
|   caught by judge, code-scored items | 1 (all runs) | 0.67 (0–1) |
|   judged-only items | 0.33 (0–1) | 1.00 (0–2) |
| Correct outcome (of 19) | 17.00 (16–18) | 16.33 (16–17) |
| Values correct (of 162) | 146.33 (144–148) | 145.00 (141–148) |
| Values wrong | 2.00 (1–3) | 3.67 (2–5) |
| Values not in the data table | 13.67 (12–17) | 13.33 (12–16) |
| Items with invented content | 1 (all runs) | 1.33 (1–2) |
| Items with conflicting rows | 0 (all runs) | 0 (all runs) |
| Judged-only items accurate (of 5) | 4.67 (4–5) | 3.67 (2–5) |
| Auto-approved (of 19) | 16.00 (15–17) | 14.67 (13–16) |
| Sent to review (of 19) | 3.00 (2–4) | 4.33 (3–6) |
| Fresh-run cost per run ($) | 2.57 (2.55–2.58) | 3.22 (2.78–3.54) |
| Cost per figure ($, run cost / 19, judge included) | 0.135 | 0.169 |

Processing errors: none in any run.

### Unsafe approvals by figure and run

Grouped as: **drawing vs. book** (Figure 2.14: the day-class box is drawn with Q1 at about 53, the
book's data give 56; a description that follows the drawing is scored wrong, `SCORING.md`),
**known category wording** (Figure 2.59: seven states and Washington, DC described as "eight states"),
and **real errors** (everything else).

**Muse Spark (Meta)**

| Group | r7 | r8 | r9 |
|---|---|---|---|
| Drawing vs. book (2.14) | 2.14 (day Q1 53, book 56) | 2.14 (day Q1 53) | 2.14 (day Q1 53) |
| Known category wording (2.59) | 2.59 ("eight states") | 2.59 ("eight states and Washington DC") | 2.59 ("8 states and DC") |
| Real errors | 2.14 ⚑: calls the 80–90 range (Q1 to Q3) "the top 50 percent"; it is the middle 50% | none | 2.47: BMW 3 series Q3 "about 43" (reference about 41); describes ticks beyond the labeled values that are not drawn |
| Total unsafe | 2 | 2 | 3 |

**Muse Spark Open (Baseten)**

| Group | r7 | r8 | r9 |
|---|---|---|---|
| Drawing vs. book (2.14) | 2.14 (day Q1 53, book 56) | 2.14 (day Q1 53) | none |
| Known category wording (2.59) | none | 2.59 ("eight states") | 2.59 ("eight states") |
| Real errors | 2.26: says the whole line "1:Edit…" is highlighted (only "1:" is); 2.44: United States box described as spreading from about 5 to 11 (box 0 to about 5, whisker to 11) | 2.1: cover lettering called sans-serif (it is serif); 2.14 ⚑: day-class median given as 77 (book 74.5) | none |
| Total unsafe | 3 | 3 | 1 |

⚑ Counted as real errors. The 2.14 exclusion covers only the drawn-vs-book Q1 difference: the day
box is drawn at about 53, so a description that follows the drawing is not the agent's mistake. These
two descriptions have a second error the drawing does not explain: Meta r7 calls the 80–90 range (Q1
to Q3) "the top 50 percent" (it is the middle 50%), and Baseten r8 gives the day-class median as 77
(the book's value is 74.5; the drawn-Q1 difference does not explain it, and 2.5 off is just past
this figure's tolerance of 2.4, so it is a narrow miss). Each of these runs' 2.14 approvals is listed once in
the totals, but counts as a real error per run. Without them (2.14 excluded whole) the rates would be
Meta 0.33 (0–1) and Baseten 1.00 (0–2).

All 2.14 and 2.59 cases were scored "code + judge"; 2.47, 2.26, 2.44 and 2.1 "judged by model"
(figures without recorded values).

**Real errors per run**, mean (min–max):

| | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| **Real errors per run (headline)** | **0.67 (0–1)**: runs 1, 0, 1 | **1.33 (0–2)**: runs 2, 2, 0 |
| For comparison: 2.14 excluded whole | 0.33 (0–1): runs 0, 0, 1 | 1.00 (0–2): runs 2, 1, 0 |

### Time and spend

| | Meta | Baseten |
|---|---|---|
| Wall time r7 / r8 / r9 | 41 / 15.5 / 15 min | 55 / 24 / 30 min |
| Uncached calls (3 runs) | 331 | 361 |
| Spent in the 3 runs (agent + judge) | $7.67 | $9.57 |

r7 ran while the other backend's r7 ran too, and both were slower; later reps overlapped less.

## Held-out set: the run

13 figures (`eval/heldout.json`: 5 from Chapter 2, PDF pages 168–175; 8 from Chapter 12, pages
630–670, including scatter plots and regression lines, types absent from the dev set). Run once per
backend on frozen code (`run_benchmark.py --set heldout --rep 1`), in parallel, after all dev runs
finished. Free-layer detection found all 13 on both backends (the same regions). Results:
`heldout_<backend>_r1.json`. The frozen scorer records status and cost only for the held-out set
(no references in heldout.json), so its console line "0 unsafe, 0/13 correct outcome" means
"not scored", not zero.

| | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Auto-approved (of 13) | 7 | 5 |
| Sent to review (of 13) | 6 | 8 |
| Not detected | 0 | 0 |
| Agent cost (uncached calls) | $0.85 (73) | $1.19 (87) |
| Cost per figure | $0.066 | $0.092 |
| Wall time | 8.5 min | 18.5 min |

Review queue per backend: Meta sent 12.2, 12.9, 12.11, 12.13, 12.20, 12.28; Baseten the same six
plus 2.63 and 12.32.

## Held-out set, reference-free judge

`scripts/judge_heldout.py` (written and committed before the held-out run): the dev judge (Muse Wonder,
`judge.md`, the figure image, temperature 0) with only what heldout.json has (caption; no type, notes or
reference). Results: `heldout_<backend>_r1_judged.json`.

| | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Judged accurate (of 13) | 13 | 12 |
| **Unsafe approvals** | **0** | **1** (12.6: panel (a) described as seven dots; it has six) |
|   all judged by model | 0 | 1 |
| Judge cost | $0.95 | $1.01 |

Without a reference the judge is lenient: it found no error in any Meta description, while the
reference scoring below fails four (the judge with references: 12.6's invented coordinates and 12.32's
axis end; the code checks: 2.63, a false alarm, and 12.2, in review).

## Held-out set, scored against checked references

References: `eval/heldout_refs.json` (`eval/make_heldout_refs.py`), written by hand from the figure crops
and the textbook text of each figure's page and the pages either side (`eval/heldout_review.zip`, free-layer
detection crops, no model calls), **without seeing any agent output**; committed (d8eaf76) before the
held-out run. Scored by `scripts/score_heldout_refs.py` with the frozen scorer's own functions, unchanged
at freeze-v1: the judge with references (`judge()`, `judge.md`, image), `score_record(..., "dev")`
(value check, invented numbers) and `summarize()`. Scatter-plot points (data.points) have no series,
so those items are scored by the judge alone. No agent re-run. Results: `heldout_<backend>_r1_refs.json`.

| | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| **Unsafe approvals** (frozen scorer) | **3** | **1** |
|   caught by code checks | 1 (2.63) | 0 |
|   caught by judge, code-scored items | 0 | 0 |
|   judged-only items | 2 (12.6, 12.32) | 1 (12.6) |
| Unsafe, excluding the scorer false alarm (2.63, below) | 2 | 1 |
| Values correct (of 38, on the 6 series figures) | 35 | 34 |
| Values wrong | 1 (12.2, in review) | 0 |
| Values not in the data table | 2 | 4 |
| Items with invented content | 2 (2.63, 12.2) | 1 (12.2) |
| Judged-only items accurate (of 7) | 5 | 5 |
| Auto-approved (of 13) | 7 | 5 |
| Sent to review (of 13) | 6 | 8 |
| Accurate among those sent to review | 5 of 6 (12.2 not) | 6 of 8 (12.2, 12.9 not) |
| Judge cost | $0.24, plus $0.92 for 10 calls in a first attempt that stopped on a network error (DNS lookup failure); their answers were cached and reused | $1.19 |

"Correct outcome" is not reported: every reference expects auto-approval, so the frozen scorer's
correct outcome equals the auto-approved count (Meta 7, Baseten 5) and counts every review as wrong.

### Unsafe approvals by figure

| Figure | Meta | Baseten | Error |
|---|---|---|---|
| 12.6 (two scatter plots, no axis numbers) | unsafe | unsafe | Meta: gives coordinates as percentages of the axes for every point, though there is no scale; Baseten: says panel (a) has seven dots (it has six) |
| 12.32 (study hours vs score) | unsafe | in review | Meta: x-axis "0 to 15" and the line "at x = 15" (the axis ends at 14) |
| 2.63 (cruise histogram) | unsafe ✱ | in review | the code check flags "10" |

✱ **Scorer false alarm.** The description says "There are 10 bars with one empty interval"; the figure
has 11 classes, one of them empty, so 10 bars is right, and the judge with the reference found no
error. The frozen invented-number check allows the number of classes (11) but not the number of drawn
bars. Kept in the frozen count above; the scorer is not changed.

12.6 has no numeric scale, the case `tools.no_numeric_scale` is meant to send to review; on both
backends it was auto-approved, so that rule did not catch it on this unseen figure type.

## Total spend

List prices, from `output/llm_calls.jsonl` (uncached calls only):

| | Meta API ($150 credits) | Baseten ($100 credits) |
|---|---|---|
| This final run (dev reps 7–9, held-out runs, both judge passes, the reference scoring, tests of the judge script) | $17.91 | $5.76 |
| Total to date | $52.99 | $20.39 |

All judge calls are billed to the Meta API, also for Baseten runs. SAM 3.1 (26 calls, all before this run)
is not priced. Elapsed time: dev runs 22:11–00:01 and held-out runs 00:01–00:20 (Oct 30–31, Meta and
Baseten in parallel); the judge passes followed. The first reference-scoring attempt stopped on a
network error after its 10th judge call (last call 00:34); it was rerun at 07:22 and finished at 07:28.
