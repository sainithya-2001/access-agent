# dev set, runs 7–9 per backend: mean (min–max)

| Metric | Muse Spark (Meta) | Muse Spark Open (Baseten) |
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

## Unsafe approvals by item (runs out of 3)

| Item | Meta | Baseten |
|---|---|---|
| fig-2.1 | 0 | 1 |
| fig-2.14 | 3 | 2 |
| fig-2.26 | 0 | 1 |
| fig-2.44 | 0 | 1 |
| fig-2.47 | 1 | 0 |
| fig-2.59 | 3 | 2 |

## Spend and failures

- meta: 331 uncached calls, $7.67 spent in these runs (agent + judge); processing errors: none
- baseten: 361 uncached calls, $9.57 spent in these runs (agent + judge); processing errors: none
