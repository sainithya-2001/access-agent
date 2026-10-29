# dev set, runs 4–6 per backend: mean (min–max)

| Metric | Muse Spark (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Unsafe approvals (target 0) | 2.67 (2–3) | 4.67 (2–8) |
|   caught by code checks | 1.33 (1–2) | 2.00 (1–4) |
|   caught by judge, code-scored items | 0.67 (0–1) | 1.67 (1–2) |
|   judged-only items | 0.67 (0–1) | 1.00 (0–2) |
| Correct outcome (of 19) | 17.33 (17–18) | 16.33 (15–18) |
| Values correct (of 162) | 148.00 (147–149) | 142.67 (142–144) |
| Values wrong | 2.00 (1–3) | 4.67 (4–6) |
| Values not in the data table | 12 (all runs) | 14.67 (12–16) |
| Items with invented content | 0.67 (0–1) | 2 (all runs) |
| Items with conflicting rows | 0 (all runs) | 0 (all runs) |
| Judged-only items accurate (of 5) | 4.33 (4–5) | 3.33 (2–5) |
| Auto-approved (of 19) | 17.00 (16–18) | 15.67 (14–17) |
| Sent to review (of 19) | 2.00 (1–3) | 3.33 (2–5) |
| Fresh-run cost per run ($) | 2.35 (2.20–2.47) | 2.83 (2.70–2.91) |

## Unsafe approvals by item (runs out of 3)

| Item | Meta | Baseten |
|---|---|---|
| fig-2.1 | 1 | 2 |
| fig-2.10 | 0 | 1 |
| fig-2.14 | 3 | 2 |
| fig-2.16 | 0 | 1 |
| fig-2.19 | 0 | 2 |
| fig-2.25 | 1 | 1 |
| fig-2.44 | 0 | 1 |
| fig-2.47 | 1 | 0 |
| fig-2.5 | 0 | 1 |
| fig-2.58 | 0 | 1 |
| fig-2.59 | 2 | 2 |

## Spend and failures

- meta: 328 uncached calls, $7.04 spent in these runs (agent + judge); processing errors: none
- baseten: 324 uncached calls, $8.37 spent in these runs (agent + judge); processing errors: none
