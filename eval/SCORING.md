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
- **Judge:** `muse-wonder-1.0` on the Meta API, the same for every backend under test.

## Metrics

**Unsafe approval** (headline, target 0): an auto-approved figure whose description is not accurate.
*Accurate* means no wrong value, no invented number, no invented data label (code-scored items), or no
factual error found by the judge (judged items), or an exact stemplot match.

**Correct outcome**
- `auto_approve` items: correct only if auto-approved.
- `needs_review_ok` items: correct if sent to review, or if auto-approved *and* accurate.

**Value accuracy** (items with true values)
- Every true value is matched to the description's data table, by series and by label.
  - Labels match as equal numbers, as ranges with endpoints within 1% of the axis range, or by
    shared words (abbreviations count, see change 2).
  - Box plots are matched by statistic: min, q1, median, q3, max.
- **Series:** the book and the description name series differently ("Final grades" vs "light blue
  line"). The scorer picks the one-to-one mapping under which the most values are correct.
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
- *Data labels* in the description's data table that match no true label.

**Judged by model** (items without true values: 2.1, 2.26, 2.30, 2.44, 2.47)
- `muse-wonder-1.0` compares the description with the checked reference and the notes, and lists
  factual errors and missing points (prompt: `app/agent/prompts/judge.md`).
- It also sees the figure image, used only to confirm details the reference leaves out; where the
  image and the reference disagree, the reference wins.
- These items are reported separately from code-scored ones.

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
- **The judge is a model:** its verdicts are reused by `--rescore`. Changing `judge.md` requires a
  new run.
