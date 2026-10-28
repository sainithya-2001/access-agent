"""Generates eval/benchmark_draft.json from the textbook. Values come from its own tables and text.

The checked file is eval/benchmark.json; this script never writes it, so hand edits there are safe.
Run from the repo root: python eval/make_benchmark.py
"""
import glob
import json
import re
import sys
from pathlib import Path

import pymupdf as fitz

sys.stdout.reconfigure(encoding="utf-8")
pdf = fitz.open(glob.glob("data/samples/*.pdf")[0])


def pts(labels, values): return [{"label": str(l), "value": v} for l, v in zip(labels, values)]
def series(name, labels, values): return {"name": name, "points": pts(labels, values)}
def counts(data, keys): return [data.count(k) for k in keys]


# Figure 2.5: heights of 100 players (Example 2.7, pp. 87-88), binned at the textbook's boundaries.
t = pdf[86].get_text(); s = t[t.find("soccer players"):]; s = s[s.find("60;"):s.find("2.2 •")]
heights = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)] + [74.0]  # 74 is on p. 88
assert len(heights) == 100
edges = [round(59.95 + 2 * i, 2) for i in range(9)]
h_rel = [round(sum(edges[i] < v < edges[i + 1] for v in heights) / 100, 2) for i in range(8)]

# Figure 2.25: Example 2.33 scores, binned at the five intervals the solution gives.
scores = [33, 42, 49, 49, 53, 55, 55, 61, 63, 67, 68, 68, 69, 69, 72, 73, 74, 78, 80, 83, 88, 88, 88, 90, 92,
          94, 94, 94, 94, 96, 100]
e25 = [32.5, 46.1, 59.7, 73.3, 86.9, 100.5]
s_cnt = [sum(e25[i] < v < e25[i + 1] for v in scores) for i in range(5)]

books = [1] * 11 + [2] * 10 + [3] * 16 + [4] * 6 + [5] * 5 + [6] * 2
sym = [4, 5, 6, 6, 6, 7, 7, 7, 7, 7, 7, 8, 8, 8, 9, 10]
terry = [7, 9, 3, 3, 3, 4, 1, 3, 2, 2]
cpi = [184.0, 188.9, 195.3, 201.6, 207.342, 215.303, 214.537, 218.056, 224.939, 229.594]
years = list(range(1855, 1876))
female = [45545, 49582, 50257, 50324, 51915, 51220, 52403, 51812, 53115, 54959, 54850, 55307, 55527, 56292,
          55033, 56431, 56099, 57472, 58233, 60109, 60146]
male = [47804, 52239, 53158, 53694, 54628, 54409, 54606, 55257, 56226, 57374, 58220, 58360, 58517, 59222,
        58321, 58959, 60029, 61293, 61467, 63602, 63432]
total = [93349, 101821, 103415, 104018, 106543, 105629, 107009, 107069, 109341, 112333, 113070, 113667, 114044,
         115514, 113354, 115390, 116128, 118765, 119700, 123711, 123578]
assert all(f + m == tt for f, m, tt in zip(female, male, total))
mids = [44.5, 54.5, 64.5, 74.5, 84.5, 94.5, 104.5]

B = []

# Items where sending the figure to human review is a correct outcome, and why.
REVIEW_OK = {
    "fig-2.9": "The two series differ only by color and the crop has no legend, so the image alone cannot say "
               "which line is test scores and which is final grades.",
    "fig-2.14": "The figure does not label which box plot is the day class and which the night class; that is "
                "only knowable from the text.",
    "fig-2.25": "The drawn bar heights do not scale with the counts computed from the data, the y-axis has no "
                "numbers, and the 73.3 / 73.5 ticks are ambiguous; a verifier should see the readings disagree.",
    "fig-2.30": "Blank axes with no data: a person should confirm that an empty-graph description is right "
                "(an auto-approved description is also fine if it invents nothing).",
}
KEY_ORDER = ["id", "page", "region_id", "caption", "type", "region_type", "title", "x_axis", "y_axis", "data",
             "values_source", "reference", "expected_outcome", "notes", "verified"]


def add(**k):
    for f in ("title", "x_axis", "y_axis", "data", "values_source"):
        k.setdefault(f, None)
    k.setdefault("notes", "")
    reason = REVIEW_OK.get(k["id"])
    k["expected_outcome"] = "needs_review_ok" if reason else "auto_approve"
    if reason:
        k["notes"] = f"Review is a correct outcome: {reason} {k['notes']}".strip()
    k["verified"] = False
    B.append({f: k[f] for f in KEY_ORDER})


add(id="fig-2.1", page=77, region_id="p77_f0",
    caption="Figure 2.1 When you have large amounts of data, you will need to organize it in a way that makes sense. "
            "These ballots from an election are rolled together with similar ballots to keep them organized.",
    type="photo", region_type="photo",
    reference={"alt_text": "Rolled paper election ballots bundled together in a pile, with the chapter title "
                           "Descriptive Statistics overlaid.",
               "long_description": ""},
    notes="Chapter opener photo. The caption already explains it, so a short alt text and no long description "
          "is the expected outcome.")

add(id="fig-2.2", page=84, region_id="p84_f0", caption="Figure 2.2", type="line_graph", region_type="chart",
    x_axis="Number of Times Teenager is Reminded", y_axis="Frequency",
    data={"series": [series("Frequency", range(6), [2, 5, 8, 14, 7, 4])]}, values_source="Table 2.7 (p. 83)",
    reference={"alt_text": "Line graph of how often 40 teenagers must be reminded to do chores; frequency peaks at "
                           "3 reminders (14 parents).",
               "long_description": "Line graph. The horizontal axis is Number of Times Teenager is Reminded, from 0 "
                                   "to 6; the vertical axis is Frequency, from 0 to 16. Points connected by line "
                                   "segments: 0 times, 2; 1 time, 5; 2 times, 8; 3 times, 14; 4 times, 7; 5 times, 4. "
                                   "The frequency rises to a peak at 3 reminders and then falls."},
    notes="The x-axis runs to 6 but there is no point at 6.")

add(id="fig-2.3", page=85, region_id="p85_f0", caption="Figure 2.3", type="bar_graph", region_type="chart",
    title="Proportion (%) of TikTok Users", x_axis="Age Groups", y_axis="Proportion (%) of TikTok Users",
    data={"series": [series("Proportion (%)", ["10–19", "20–29", "30–39", "40–49", "50+"],
                            [32.5, 29.5, 16.4, 13.9, 7.1])]},
    values_source="Table 2.9 (pp. 84–85)",
    reference={"alt_text": "Bar graph of U.S. TikTok users by age group; the share falls from 32.5% for ages 10–19 "
                           "to 7.1% for ages 50 and over.",
               "long_description": "Bar graph titled Proportion (%) of TikTok Users. The horizontal axis is Age "
                                   "Groups and the vertical axis is Proportion (%) of TikTok Users, from 0% to 35%. "
                                   "Ages 10–19: 32.5%. Ages 20–29: 29.5%. Ages 30–39: 16.4%. Ages 40–49: 13.9%. "
                                   "Ages 50+: 7.1%. Each older age group has a smaller share than the one before."})

add(id="fig-2.5", page=89, region_id="p89_f0", caption="Figure 2.5", type="histogram", region_type="chart",
    x_axis="Heights", y_axis="Relative Frequency",
    data={"series": [series("Relative frequency", [f"{edges[i]:.2f}–{edges[i + 1]:.2f}" for i in range(8)],
                            h_rel)]},
    values_source="Example 2.7 data list (pp. 87–88), binned at the boundaries given on p. 88; the bars are also "
                  "labeled with these values",
    reference={"alt_text": "Histogram of the heights of 100 soccer players; the tallest bar, 0.40, is for 65.95 to "
                           "67.95 inches.",
               "long_description": "Histogram of the heights, in inches, of 100 semiprofessional soccer players. "
                                   "The horizontal axis is Heights, with class boundaries every 2 inches from 59.95 "
                                   "to 75.95; the vertical axis is Relative Frequency, from 0 to 0.4. Each bar is "
                                   "labeled with its value: 59.95–61.95, 0.05; 61.95–63.95, 0.03; 63.95–65.95, "
                                   "0.15; 65.95–67.95, 0.40; 67.95–69.95, 0.17; 69.95–71.95, 0.12; 71.95–73.95, "
                                   "0.07; 73.95–75.95, 0.01. Most heights are between 63.95 and 71.95 inches, with "
                                   "a single peak at 65.95–67.95."},
    notes="Values are printed on the bars, so they must match exactly. The x tick labels are rotated.")

add(id="fig-2.6", page=90, region_id="p90_f0", caption="Figure 2.6", type="histogram", region_type="chart",
    x_axis="Number of Books", y_axis="Frequency",
    data={"series": [series("Frequency", ["1", "2", "3", "4", "5", "6"], counts(books, [1, 2, 3, 4, 5, 6]))]},
    values_source="Example 2.8 data list and sentence (p. 89)",
    reference={"alt_text": "Histogram of books bought by 50 part-time students; 3 books is most common "
                           "(16 students).",
               "long_description": "Histogram of the number of books bought by 50 part-time college students. The "
                                   "horizontal axis is Number of Books, with bar boundaries at 0.5, 1.5, 2.5, 3.5, "
                                   "4.5, 5.5 and 6.5; the vertical axis is Frequency, from 0 to 16. 1 book, 11 "
                                   "students; 2 books, 10; 3 books, 16; 4 books, 6; 5 books, 5; 6 books, 2. The "
                                   "distribution peaks at 3 books and tails off toward 6."},
    notes="Values are not printed on the bars; read off the axis.")

add(id="fig-2.9", page=94, region_id="p94_f0", caption="Figure 2.9", type="frequency_polygon", region_type="chart",
    title="Final Test Grade v Final Grade", x_axis="Grades", y_axis="Frequency",
    data={"series": [series("Final test scores", mids, [0, 5, 10, 30, 40, 15, 0]),
                     series("Final grades", mids, [0, 10, 10, 30, 45, 5, 0])]},
    values_source="Tables 2.16 and 2.17 (p. 94), plotted at class midpoints, with the empty end classes the text "
                  "describes for Figure 2.8",
    reference={"alt_text": "Two overlaid frequency polygons comparing calculus final test scores and final grades; "
                           "both peak at 84.5.",
               "long_description": "Two frequency polygons drawn on the same axes, titled Final Test Grade v Final "
                                   "Grade. The horizontal axis is Grades, at class midpoints from 44.5 to 104.5; the "
                                   "vertical axis is Frequency, from 0 to 50. Final test scores: 44.5, 0; 54.5, 5; "
                                   "64.5, 10; 74.5, 30; 84.5, 40; 94.5, 15; 104.5, 0. Final grades: 44.5, 0; 54.5, "
                                   "10; 64.5, 10; 74.5, 30; 84.5, 45; 94.5, 5; 104.5, 0. Both rise to a peak at "
                                   "84.5. Final grades have more values at 54.5 and 84.5 and fewer at 94.5 than "
                                   "test scores."},
    notes="Two series, told apart only by color (dark and light blue), with no legend in the crop. Which line is "
          "which comes from the tables: light blue = final grades (10 at 54.5, 45 at 84.5).")

add(id="fig-2.10", page=96, region_id="p96_f0", caption="Figure 2.10", type="time_series", region_type="chart",
    title="Annual CPI", x_axis="Year", y_axis="Annual Consumer Price Index",
    data={"series": [series("Annual CPI", range(1, 11), cpi)]}, values_source="Table 2.20, Annual column (p. 96)",
    reference={"alt_text": "Time series of the annual Consumer Price Index over 10 years, rising from 184.0 in "
                           "year 1 to 229.6 in year 10.",
               "long_description": "Time series graph titled Annual CPI. The horizontal axis is Year, 1 to 10; the "
                                   "vertical axis is Annual Consumer Price Index, from 180 to 240. Year 1, 184.0; "
                                   "year 2, 188.9; year 3, 195.3; year 4, 201.6; year 5, 207.3; year 6, 215.3; year "
                                   "7, 214.5; year 8, 218.1; year 9, 224.9; year 10, 229.6. The index rises every "
                                   "year except for a small dip from year 6 to year 7."},
    notes="Values in the figure must be read off the axis; the textbook values have up to 3 decimals.")

add(id="fig-2.11", page=107, region_id="p107_f0", caption="Figure 2.11", type="box_plot", region_type="chart",
    data={"boxes": [{"name": None, "min": 1, "q1": 2, "median": 7, "q3": 9, "max": 11.5}],
          "values": [1, 1, 2, 2, 4, 6, 6.8, 7.2, 8, 8.3, 9, 10, 10, 11.5]},
    values_source="Data and five-number summary in the text (p. 107)",
    reference={"alt_text": "Box plot on a number line from 1 to 11.5: minimum 1, first quartile 2, median 7, third "
                           "quartile 9, maximum 11.5.",
               "long_description": "Box plot above a horizontal number line marked from 1 to 11.5. The left "
                                   "whisker runs from the minimum, 1, to the first quartile, 2. The box runs from 2 "
                                   "to the third quartile, 9, with the median at 7 shown as a dashed line. The right "
                                   "whisker runs from 9 to the maximum, 11.5. The left part of the box (2 to 7) is "
                                   "much wider than the right part (7 to 9)."})

add(id="fig-2.14", page=109, region_id="p109_f1", caption="Figure 2.14", type="box_plot", region_type="chart",
    data={"boxes": [{"name": "Day class", "min": 32, "q1": 56, "median": 74.5, "q3": 82.5, "max": 99},
                    {"name": "Night class", "min": 25.5, "q1": 78, "median": 81, "q3": 89, "max": 98}]},
    values_source="Example 2.24 solution, parts a and b (p. 109)",
    reference={"alt_text": "Two box plots of statistics test scores on one number line, day class above night "
                           "class; the night class box is narrower and higher.",
               "long_description": "Two box plots on one number line from 20 to 100. Upper plot (day class): "
                                   "minimum 32, first quartile 56, median 74.5, third quartile 82.5, maximum 99. "
                                   "Lower plot (night class): minimum 25.5, first quartile 78, median 81, third "
                                   "quartile 89, maximum 98. The day class has a much wider box, so the middle 50% "
                                   "of its scores are more spread out. The night class scores are concentrated "
                                   "higher, but it has a long left whisker down to 25.5."},
    notes="The figure itself does not label which plot is day and which is night; the order (day on top) comes "
          "from matching the summaries. A good description should say the labels are not shown, or use the text.")

add(id="fig-2.16", page=116, region_id="p116_f0", caption="Figure 2.16", type="histogram", region_type="chart",
    data={"series": [series("Frequency", range(4, 11), counts(sym, list(range(4, 11))))], "values": sym},
    values_source="Data list in the text (p. 116)",
    reference={"alt_text": "Symmetrical histogram of values 4 to 10, peaking at 7; the mean, median and mode are "
                           "all 7.",
               "long_description": "Histogram of 16 values with one bar for each whole number from 4 to 10. "
                                   "Frequencies: 4, 1; 5, 1; 6, 3; 7, 6; 8, 3; 9, 1; 10, 1. The shape is "
                                   "symmetrical around 7, which is the single peak. The vertical axis has gridlines "
                                   "but no numbers or title."},
    notes="No axis titles and no y-axis numbers: values can only be checked against the text. A description that "
          "gives exact counts read from the image is guessing.")

add(id="fig-2.19", page=117, region_id="p117_f1", caption="Figure 2.19 Terry’s distribution has a right (positive) skew.",
    type="dot_plot", region_type="chart", title="Terry’s Letter Count",
    data={"series": [series("Count", range(1, 11), counts(terry, list(range(1, 11))))], "values": sorted(terry)},
    values_source="Example 2.31 data list (p. 117)",
    reference={"alt_text": "Dot plot of Terry's letter counts on a 1 to 10 scale, clustered at 1 to 4 with a peak "
                           "at 3 and two high values, 7 and 9.",
               "long_description": "Dot plot titled Terry’s Letter Count, on a number line from 1 to 10, with one X "
                                   "per value. 1: one X. 2: two. 3: four. 4: one. 7: one. 9: one. No values at 5, 6, "
                                   "8 or 10. Most values sit at 1 to 4; the two values at 7 and 9 give the "
                                   "distribution a right (positive) skew."})

add(id="fig-2.25", page=124, region_id="p124_f0", caption="Figure 2.25", type="box_plot_histogram", region_type="chart",
    data={"boxes": [{"name": None, "min": 33, "q1": 61, "median": 73, "q3": 90, "max": 100}],
          "series": [series("Frequency", [f"{e25[i]}–{e25[i + 1]}" for i in range(5)], s_cnt)], "values": scores},
    values_source="Example 2.33 data and solution (pp. 123–124); histogram counts computed from the data at the "
                  "solution's interval endpoints",
    reference={"alt_text": "Box plot drawn above a histogram of 31 exam scores from 32.5 to 100.5; the highest bar "
                           "is 86.9 to 100.5.",
               "long_description": "A box plot and a histogram on the same horizontal axis, marked 32.5, 46.1, "
                                   "59.7, 73.3, 73.5, 86.9 and 100.5, for 31 first-exam scores. Box plot: minimum "
                                   "33, first quartile 61, median 73, third quartile 90, maximum 100. Histogram with "
                                   "five intervals of width 13.6: 32.5–46.1, 2 scores; 46.1–59.7, 5; 59.7–73.3, 9; "
                                   "73.3–86.9, 4; 86.9–100.5, 11. The scores are skewed left: most are high, with a "
                                   "long tail of lower scores."},
    notes="Check: the drawn bar heights do not scale exactly with these counts, and the y-axis has no numbers. The "
          "axis shows both 73.3 and 73.5; the text gives median 73 and mean 73.5. Confirm what the dashed line and "
          "the 73.5 tick mark.")

add(id="fig-2.26", page=127, region_id="p127_f0", caption="Figure 2.26", type="calculator_screenshot",
    region_type="text_image",
    data={"text": ["EDIT  CALC  TESTS", "1:Edit…", "2:SortA(", "3:SortD(", "4:ClrList", "5:SetUpEditor"]},
    values_source="Read from the screenshot; the step text above it says 'press the STAT key and select 1:Edit'",
    reference={"alt_text": "TI calculator screen: the STAT menu with the EDIT tab selected and option 1:Edit "
                           "highlighted.",
               "long_description": "Graphing calculator screen showing the STAT menu. Tabs across the top: EDIT "
                                   "(selected), CALC, TESTS. Options: 1:Edit…, 2:SortA(, 3:SortD(, 4:ClrList, "
                                   "5:SetUpEditor. Option 1 is highlighted."},
    notes="Text image, not a chart. Expected: transcribe the screen; no data table.")

add(id="fig-2.30", page=130, region_id="p130_f0", caption="Figure 2.30", type="blank_axes", region_type="chart",
    x_axis="Number of Pairs of Shoes", y_axis="Frequency",
    reference={"alt_text": "Empty graph axes for sketching a histogram: Frequency on the vertical axis, Number of "
                           "Pairs of Shoes on the horizontal axis.",
               "long_description": "Blank axes for students to draw their own histogram in the stats lab. The "
                                   "vertical axis is labeled Frequency and the horizontal axis Number of Pairs of "
                                   "Shoes. There are no scales, bars or data."},
    notes="Trap figure: there are no data. Any number or trend in the description is invented.")

add(id="fig-2.44", page=154, region_id="p154_f2", caption="Figure 2.44", type="box_plot", region_type="chart",
    reference={"alt_text": "Box plots of foreign countries visited by 20-year-olds in China, Germany and the U.S., "
                           "on a 0 to 11 scale.",
               "long_description": "Three box plots on one number line from 0 to 11, showing how many foreign "
                                   "countries 20-year-olds had visited. China: only a line from 0 to 5 is drawn, "
                                   "with no box. Germany: whiskers from 0 to 11, box from about 4 to 8, with the "
                                   "median at the right edge of the box, about 8. United States: box from 0 to about "
                                   "5 with the median at about 2, and a whisker out to 11. Germany has the highest "
                                   "median; the United States has the most values at the low end."},
    notes="No numbers in the text; values are null. The numbers in the draft are read by eye from the figure. China "
          "is drawn without a box, which is an unusual case worth describing as drawn.")

add(id="fig-2.47", page=156, region_id="p156_f0", caption="Figure 2.47", type="box_plot", region_type="chart",
    reference={"alt_text": "Box plots of buyers' ages for BMW 3, 5 and 7 series cars on a 25 to 80 scale; the 7 "
                           "series has the oldest buyers.",
               "long_description": "Three box plots on one number line from 25 to 80, showing the ages of 130 "
                                   "buyers each of new BMW 3, 5 and 7 series cars. 3 series: whiskers from about 23 "
                                   "to about 66, box from about 28 to 41, median about 33. 5 series: whiskers from "
                                   "about 32 to about 63, box from about 39 to 55, median about 41. 7 series: "
                                   "whiskers from about 36 to about 67, box from about 41 to 59, median about 46. "
                                   "All three are skewed right; median age rises from the 3 series to the 7 series."},
    notes="No exact values in the text; values are null. The draft numbers are read by eye. The solutions (p. 176) "
          "give only estimates for the 5 series: second quarter about 3 years, third quarter about 14 years, IQR "
          "about 17 years.")

add(id="fig-2.58", page=171, region_id="p171_f0", caption="Figure 2.58", type="time_series", region_type="chart",
    title="Births in Scotland", x_axis="Year", y_axis="Number of Births",
    data={"series": [series("Both sexes", years, total), series("Males", years, male),
                     series("Females", years, female)]},
    values_source="Exercise 21, Tables 2.50–2.52 (pp. 139–140)",
    reference={"alt_text": "Time series of births in Scotland, 1855–1875: total births rise from 93,349 to "
                           "123,578, with male and female lines below.",
               "long_description": "Time series graph titled Births in Scotland, with three lines for 1855 to "
                                   "1875. The horizontal axis is Year; the vertical axis is Number of Births, from "
                                   "40,000 to 130,000. Both sexes: 93,349 in 1855, rising to 123,711 in 1874 and "
                                   "123,578 in 1875. Males: 47,804 in 1855 to 63,432 in 1875. Females: 45,545 in "
                                   "1855 to 60,146 in 1875. All three rise over the period with small dips, for "
                                   "example in 1860 and 1869. Male births are slightly above female births every "
                                   "year. The full yearly values are in the data table."},
    notes="Small text, 21 x-axis labels rotated, three series told apart by color and a legend at the bottom. "
          "Hardest chart in the set to read.")

add(id="fig-2.59", page=173, region_id="p173_f0", caption="Figure 2.59", type="bar_graph", region_type="chart",
    y_axis="Percent (%)",
    data={"series": [series("Percent", ["Arkansas", "Washington DC", "Idaho", "Maryland", "Michigan", "Mississippi",
                                        "Virginia", "Wyoming"],
                            [30.1, 22.2, 26.5, 27.1, 30.9, 34.0, 26.0, 25.1])]},
    values_source="Solution 75a (pp. 172–173); the measure is from Exercise 75 (p. 147, Table 2.62)",
    reference={"alt_text": "Bar graph of the percent of adults never married in 8 sampled states; Mississippi is highest at "
                           "34.0%, Washington DC lowest at 22.2%.",
               "long_description": "Bar graph of the percent of adults who were never married, for a random sample "
                                   "of 8 states and Washington DC. The vertical axis is "
                                   "Percent (%), from 0 to 40. Arkansas 30.1; Washington DC 22.2; Idaho 26.5; "
                                   "Maryland 27.1; Michigan 30.9; Mississippi 34.0; Virginia 26.0; Wyoming 25.1. "
                                   "Values range from 22.2 to 34.0, mostly between 25 and 31."},
    notes="The x labels are rotated and DC is written 'Wash DC'. The figure does not say what the percent "
          "measures (adults never married); that comes from Exercise 75.")

add(id="table-2.1", page=78, region_id="p78_t0", caption="Table 2.1 Stem-and- Leaf Graph", type="stem_and_leaf",
    region_type="table",
    data={"values": scores,
          "stems": {"3": "3", "4": "299", "5": "355", "6": "1378899", "7": "2348", "8": "03888", "9": "0244446",
                    "10": "0"}},
    values_source="Example 2.1 data list (p. 78)",
    reference={"alt_text": "Stem-and-leaf plot of 31 exam scores from 33 to 100.",
               "long_description": "Stem-and-leaf plot of 31 first-exam scores; each stem is the tens digit and "
                                   "each leaf the ones digit. 3 | 3. 4 | 2 9 9. 5 | 3 5 5. 6 | 1 3 7 8 8 9 9. "
                                   "7 | 2 3 4 8. 8 | 0 3 8 8 8. 9 | 0 2 4 4 4 4 6. 10 | 0. Data values: 33, 42, 49, "
                                   "49, 53, 55, 55, 61, 63, 67, 68, 68, 69, 69, 72, 73, 74, 78, 80, 83, 88, 88, 88, "
                                   "90, 92, 94, 94, 94, 94, 96, 100. Most scores are in the 60s to 90s."},
    notes="Expanded in code by app/stemplot.py, no model; region.stem_leaf must equal data.values.")

for b in B:
    assert len(b["reference"]["alt_text"]) <= 150, (b["id"], len(b["reference"]["alt_text"]))
out = {"description": "Benchmark figures from OpenStax Introductory Statistics 2e, Chapter 2 (CC BY 4.0). True "
                      "values come from the textbook's own tables and text; null where the book gives none. "
                      "Reference descriptions are drafts. Every entry needs a human check (verified: false). "
                      "expected_outcome: auto_approve = the agent should approve it; needs_review_ok = sending it to "
                      "human review also counts as correct.",
       "source_pdf": "data/samples/introductory-statistics-2e_-_WEB.pdf",
       "figures": B}
Path("eval").mkdir(exist_ok=True)
Path("eval/benchmark_draft.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(len(B), "entries; heights", h_rel, "scores bins", s_cnt)
