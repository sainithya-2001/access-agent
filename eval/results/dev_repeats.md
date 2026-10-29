# dev set, runs 1–3 per backend: mean (min–max)

| Metric | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Unsafe approvals (target 0) | 3.67 (3–4) | 3.33 (3–4) |
|   caught by code checks | 2.33 (2–3) | 1.33 (1–2) |
|   caught by judge, code-scored items | 1.33 (1–2) | 1 (all runs) |
|   judged-only items | 0 (all runs) | 1 (all runs) |
| Correct outcome (of 19) | 16.67 (16–18) | 16.00 (15–17) |
| Values correct (of 162) | 140.00 (138–143) | 140.67 (138–144) |
| Values wrong | 4.33 (2–6) | 2.67 (2–3) |
| Values not in the data table | 17.67 (17–19) | 18.67 (16–21) |
| Items with invented content | 0.33 (0–1) | 1.00 (0–2) |
| Items with conflicting rows | 0.67 (0–1) | 0 (all runs) |
| Judged-only items accurate (of 5) | 5 (all runs) | 3.33 (2–4) |
| Auto-approved (of 19) | 17 (all runs) | 15.00 (14–16) |
| Sent to review (of 19) | 2 (all runs) | 4.00 (3–5) |
| Fresh-run cost per run ($) | 2.59 (2.46–2.72) | 3.44 (3.28–3.62) |

## Unsafe approvals by item (runs out of 3)

| Item | Meta | Baseten |
|---|---|---|
| fig-2.1 | 0 | 1 |
| fig-2.14 | 3 | 3 |
| fig-2.16 | 1 | 0 |
| fig-2.19 | 2 | 1 |
| fig-2.25 | 2 | 0 |
| fig-2.44 | 0 | 1 |
| fig-2.47 | 0 | 1 |
| fig-2.59 | 3 | 3 |

## Spend and failures

- meta: 220 uncached calls, $4.80 spent in these runs (agent + judge); processing errors: none
- baseten: 331 uncached calls, $8.32 spent in these runs (agent + judge); processing errors: none
