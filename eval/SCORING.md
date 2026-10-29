# How the benchmark is scored

`scripts/run_benchmark.py --backend meta|baseten --set dev|heldout` runs the full agent on every item
and writes `eval/results/<set>_<backend>.json`. `--rescore` scores saved results again with no model
calls; it reproduces a fresh run's scores exactly (checked on both dev runs).

Truth is the checked `eval/benchmark.json` (all 19 items verified against their figure images).
Where the drawing differs from the book's numbers, **the book is the truth** (see the item notes).

## Setup
- **Detection:** free layers only (no Muse Spark page check, no SAM), so every backend describes
  exactly the same regions. All 19 benchmark regions are found this way.
- **Agent:** the normal pipeline (`FigureAgent`), on the backend under test.
- **Judge:** `muse-wonder-1.0` on the Meta API at temperature 0, the same for every backend under test.
- **Repeats:** `--rep N` gives run N its own cache namespace (so no run reuses another's or an
  earlier run's answers), crop folder and results file (`<set>_<backend>_rN.json`). Runs in parallel
  share the call log; each counts only lines with its own process id.
  `scripts/summarize_repeats.py` reports mean and range per metric.

## Metrics

**Unsafe approval** (headline, target 0): an auto-approved figure whose description is not accurate.
*Accurate* means:
- items with true values: the code checks pass (no wrong value, no conflicting data-table row, no
  invented number or data label) **and** the judge finds no factual error in the prose (change 9);
- items without true values: the judge finds no factual error;
- the stemplot: an exact match.

Unsafe approvals are reported by what caught them: code checks, the judge on a code-scored item, or
the judge on a judged-only item.

**Correct outcome**
- `auto_approve` items: correct only if auto-approved.
- `needs_review_ok` items: correct if sent to review, or if auto-approved *and* accurate.

**Value accuracy** (items with true values)
- Every true value is matched to the description's data table, by series and by label.
  - Labels match as equal numbers, as ranges with endpoints within 1% of the axis range, as a range
    and its midpoint ("0.5 to 1.5" is the bar labelled "1"), or by shared words (abbreviations count,
    change 2). Labels that both contain numbers must contain the same numbers (change 7).
  - Box plots are matched by statistic: min, q1, median, q3, max, by name or by the drawn part
    ("left whisker end", "right edge of box", change 4).
- **Series:** the book and the description name series differently ("Final grades" vs "light blue
  line"). A description series named like a true series is that series; the others get the
  one-to-one mapping under which the most values are correct (change 6).
- **Every row counts:** a data-table row with a true label that is left over after matching (a second
  row for one label, a series mapped to nothing) must agree with a true value at that label, or it
  is a *conflicting row* and the item is not accurate (change 8).
- **Tolerance:**
  - Values printed on the figure: within 0.5% of the true value (only Figure 2.5 is printed, per its notes).
  - Values read off the axis: within 3% of the axis range. The range is the smallest "from X to Y" in
    the checked reference that contains the values; otherwise the span of the values (from 0 for bars
    and points, min to max for box plots). Each series or box uses its own axis.
- **Result per true value:** correct, wrong, or missing (not in the data table). Missing is not an
  error; it is reported.
- **Titles:** the title and axis titles must be mentioned (loose word match, 60%; the verifier's rule).

**Invented content**
- *Numbers* in the alt text or long description that are not near any true value, label number,
  number in the caption, title, axis titles or reference, or tick on an axis named in the reference.
  - Ticks are every value a "nice" scale (steps of 1, 2, 2.5 or 5 × 10ⁿ, 4 to 20 ticks) could show,
    plus the step itself.
  - "Figure 2.9" or "Table 2.17" are not counted.
  - Counts of points, series or boxes are allowed.
  - A number in the page text the agent was given (caption plus nearby text, stored with each result)
    is allowed, exactly (change 5).
- *Data labels* in the description's data table that match no true label.

**Judge** (every dev item since change 9; the only check for 2.1, 2.26, 2.30, 2.44, 2.47)
- `muse-wonder-1.0` compares the description with the checked reference and the notes, and lists
  factual errors and missing points (prompt: `app/agent/prompts/judge.md`).
- It also sees the figure image, used only to confirm details the reference leaves out; where the
  image and the reference disagree, the reference wins.
- It sees the page text the agent was given: a correct statement from it is not an error.
- Its tolerance matches the code: a value read off an axis is wrong only if more than about 3% of the
  axis range off; printed values must be exact; comparisons must be right.
- Judged-only items are reported separately from code-scored ones.

**Stemplot** (table 2.1): expanded in code by `app/stemplot.py`, compared exactly with the true values.

**Review effort:** the share of figures sent to a person, compared with reviewing 100%.

**Cost**
- Tokens from the API's usage numbers, at list prices (USD per million tokens, in/out):
  - Muse Spark 1.1: $1.25 / $4.25
  - Muse Spark Open on Baseten: $1.25 / $4.25
  - Muse Wonder 1.0: $4 / $20
  - These are from third-party trackers as of Oct 2026, not Meta's or Baseten's own pricing pages.
- *Cost this run* counts only uncached calls.
- *Fresh-run cost* also prices cached calls:
  - from the original call's tokens, which cache entries record from Oct 28 on;
  - otherwise from the average for that model and step (the count of estimated calls is reported).

## Scorer changes after the first full Meta run
The first full dev run on Meta reported 6 unsafe approvals. I checked each flagged item by hand.
Three were scorer errors, and the judge change also flipped a fourth (2.30). After these changes the
same Meta outputs score 2 unsafe approvals (2.14, 2.25). Both final runs use this scorer.

1. **Tick steps count as ticks.** Figure 2.58's description says the y-axis runs "from 40,000 to
   130,000 in increments of 5,000". "5,000" was counted as an invented number. It describes the
   scale, so the step of every nice scale is now allowed.
2. **Abbreviated labels match.** Figure 2.59's chart writes "Wash DC"; the truth says "Washington
   DC". Word matching needed ≥ 60% shared words, so the bar counted as an invented label *and* as a
   missing value. Now a word matches another it is a prefix of (3+ letters).
3. **The judge sees the image.** Text-only, the judge counted every true detail the reference leaves
   out as an error: Figure 2.26's calculator screen colour and highlighting. With the image, a detail
   is an error only if it contradicts the reference or the image doesn't show it.
   - **Side effect:** Figure 2.30's description reports cut-off text above the axes ("Survey
     Results"). Text-only, the judge called that an error. With the image it accepts it, because
     the description calls it partially visible text, not the graph's title. The item notes say
     that text is not part of the graph, so this is a judgment call that went in the system's favour.

Fixed in a 6-item trial before the first full run:
- a description series with no name was treated as "no series" in the mapping;
- "from 0 to 0.4." failed to parse because of the full stop;
- one axis range was used for a figure's box plot and histogram together.

## Known limits
- **Book vs drawing:** a description that reads the *drawing* correctly can still score as wrong.
  Figure 2.14's day box is drawn at about 53 against the book's 56, and its tolerance is 2.4.
- **Missing values aren't penalised:** a description that omits values is not marked inaccurate,
  only reported as missing.
- **Temperature 0 isn't deterministic on Baseten:** the API accepts it, but the same prompt still
  gave different answers (2 of 3 the same in a test on Oct 29). Muse Spark and Muse Wonder on the
  Meta API gave the same answer 3 of 3. Hence the repeated runs.
- **The judge is a model:** its verdicts are reused by `--rescore`. Changing `judge.md` requires a
  new run.
- **Dot plots tabulated one row per dot:** Meta's Figure 2.19 data table lists each X as its own row
  ("3: 3" four times) rather than a count per value. The counts are all right, and the judge passes
  the prose, but the scorer reads each row as a count, so the item scores as wrong with conflicting
  rows. For a reader the table is confusing too, so the item stays counted as unsafe.

## Scorer changes on Oct 29 (dev runs only; before any held-out run)
Changes 4 and 5 fix the two limits seen after the no-numeric-scale rerun. Changes 6–9 come from an
audit for leniency: places where an item could score as accurate when it isn't. Each was checked on
the saved dev results and on a constructed case.

4. **Box-plot part names.** A data table may name the drawn part instead of the statistic. Meta's
   Figure 2.11 gave all five values right as "left whisker end", "left edge of box", "dashed line
   inside box" and so on, and scored 0/5 with five invented labels. Part names now map to statistics
   (box edges before whisker ends). The mapping is written in the scorer, not imported from the agent,
   so an agent bug can't hide itself in the score.
5. **Numbers from the page text.** The agent may quote the text around the figure. Figure 2.25's
   "the page text says the y-axis goes from –2.4 to 15" was scored as two invented numbers. Numbers
   in the page text the agent was given are now allowed, exact matches only.
6. **Leniency: swapped series names.** The agreement mapping let a description that swaps two series
   (calls the day class the night class) score as fully correct, because it picked whichever mapping
   made the most values right. A description series named like a true series is now held to it.
   Constructed case: the same values with the names swapped went from 10/10 correct to 2 correct, 8 wrong.
7. **Leniency: labels matched by stray words.** Word matching split numbers into words, so "1.5 to
   2.5" matched the bar labelled "1", and "BMW 3 series" matched "BMW 5 series" (2 of 3 words).
   Labels that both contain numbers must now contain the same numbers, compared whole; a range
   matches the number at its midpoint. Figure 2.6's bins ("0.5 to 1.5" for bar 1) still match.
8. **Leniency: unchecked data-table rows.** Only the first row per label was compared; a second row
   for the same label, or the rows of a description series mapped to nothing, were ignored even if
   wrong. Such rows must now agree with a true value at their label (*conflicting rows*).
9. **Leniency: the prose was barely checked on code-scored items.** A number in a sentence only had
   to be near *some* allowed number (with 63 true values almost anything is), and comparisons ("the
   tallest bar", "about twice as tall") weren't checked at all, which matters now that charts with no
   numeric scale are described by comparison. The judge now grades every dev item, and an item with
   true values is accurate only if the code checks and the judge both pass. The judge also sees the
   page text (change 5), has the code's tolerance, and runs at temperature 0.

## Repeated dev runs, Oct 29: hand check of the unsafe approvals
Three runs per backend (`--rep 1..3`, each in its own cache namespace), summarised in
`results/dev_repeats.md`. Each unsafe approval was checked against the figure image:

| Cause | Meta (runs out of 3) | Baseten (runs out of 3) |
|---|---|---|
| Matches the drawing, not the book: 2.14 day Q1 drawn at about 53, book 56 | 3 | 3 |
| Wording: 2.59 says "eight states"; it is 7 states and Washington DC | 3 | 3 |
| Real reading error: 2.25 box edges (59.7 and 86.9; drawn about 64 and 90) | 2 | 0 |
| Real reading error: 2.19 X's miscounted (3 at 2, 5 at 3) | 0 | 1 |
| Real reading error: 2.44 China line 0 to 4.5 (drawn 0 to 5) | 0 | 1 |
| Wrong comparison: 2.16 bar 6 "about twice" the shortest (3x), bar 7 "four to five times" (6x) | 1 | 0 |
| Invented detail: 2.47 minor ticks; 2.1 "many rolls with white bands" (one roll) | 0 | 2 |
| Table format: 2.19 one row per dot (see Known limits) | 2 | 0 |

Without 2.14 (drawing vs book), real errors that got through: Meta 3, 2, 3 per run (2.59 every run);
Baseten 2, 3, 2 (2.59 every run). These runs used the crops from before the neighbouring-caption fix
(commit 38cef0e), which changes the agent's recrops of 2.3, 2.4, 2.7, 2.9 and 2.10.

Dev items share PDF pages with the held-out set only on page 173 (dev 2.59 and one held-out figure).
The runner runs the agent on dev regions only; the free detection layer (no model calls) crops every
region on the page.

## Dev runs 4–6 (fixes A, B, C, D, F, G), Oct 29: hand check of the unsafe approvals
Three more runs per backend (`--rep 4..6`), with the reread-before-rewrite rule, the count, comparison
and duplicate-row checks, fix F (page-text values stated as the text's), and the neighbouring-caption
crop fix. Summary in `results/dev_repeats_r4-6.md`. Every unsafe approval was checked against the
figure image (2.58 and 2.59 against the book's values, since their pages fall in the held-out page range):

| Cause | Meta (runs out of 3) | Baseten (runs out of 3) |
|---|---|---|
| Matches the drawing, not the book: 2.14 (accurate per the judge; drawn Q1 about 53, book 56) | 3 | 2 |
| Wording: 2.59 "eight states"; it is 7 states and Washington DC | 2 | 2 |
| Real: 2.25 box edges snapped to tick labels (Q1 59.7, Q3 86.9; drawn about 64 and 90) | 1 | 1 |
| Real: 2.19 X's miscounted (3 at 2, 5 at 3; the figure has 2 and 4), both readings agreed | 0 | 2 |
| Real: 2.16 "bars at 6 and 8 one-third as tall as 7" (half) | 0 | 1 |
| Real: 2.10 year 3 read as 193 (book 195.3), both readings agreed | 0 | 1 |
| Real: 2.44 "China lowest"; China is a bare 0–5 line with no median to compare | 0 | 1 |
| Real: 2.5 "decrease to both sides of the peak"; the left end rises from 0.03 to 0.05 | 0 | 1 |
| Real: 2.58 a "dip" in 1867; the book rises from 113,667 to 114,044 | 0 | 1 |
| Real, minor detail: 2.1 title called sans-serif (it is serif) and the banner centred (left) | 1 | 2 |
| Real, minor detail: 2.47 minor ticks that are not drawn | 1 | 0 |

Real errors per run (without 2.14 and 2.59): Meta 1, 0, 2 (runs 1–3: 2, 1, 2); Baseten 2, 6, 2
(runs 1–3: 1, 2, 1). 2.19's one-row-per-dot table and 2.16's "about twice" no longer occur.

Baseten run 5 (8 unsafe approvals): 2.14 and 2.59 as in other runs, plus six real errors. Five of
the six passed the first verify at 0.96 with no issue raised; this run had the fewest first-verify
failures of any Baseten run (6 of 19; runs 1–6: 9, 11, 10, 7, 6, 7). Baseten is not deterministic at
temperature 0, so the same figure is caught in one run and passed in the next. In three of the six
(2.19, 2.25, 2.10) the independent reading made the same misreading as the description, so the
cross-check had nothing to find. 2.16 shows a gap in the code checks: for a chart with no numeric
scale, `code_checks` returns before the count and comparison checks. Run 5's description gave no
numbers (as the no-scale prompt asks), so only its ratio was wrong, and that ratio was never checked;
replayed from the cache, the comparison check flags it.

Also found: when a zoomed re-read is within tolerance of both earlier readings, `_reread` credits the
description, not the nearer reading (2.25, Q3: read 86.9, extraction 86.9, description 90). It
changed no outcome in these runs (the re-read value replaces the extracted value either way).
Neither this nor the no-scale gap is fixed in the code these runs used.
