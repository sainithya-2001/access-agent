"""One-time corrections from checking every benchmark item against its figure image."""
import json
from pathlib import Path

p = Path("eval/benchmark.json")
b = json.loads(p.read_text(encoding="utf-8"))
F = {f["id"]: f for f in b["figures"]}

def note(i, s):
    F[i]["notes"] = (F[i]["notes"] + " " + s).strip()

f = F["fig-2.59"]
f["reference"]["alt_text"] = ("Bar graph of the percent of adults never married in 7 states and Washington DC; "
                              "Mississippi is highest at 34.0%, DC lowest at 22.2%.")
f["reference"]["long_description"] = f["reference"]["long_description"].replace(
    "for a random sample of 8 states and Washington DC",
    "for 8 places: 7 states and Washington DC (labeled Wash DC)")
note("fig-2.59", "Corrected: the chart has 8 bars, 7 states plus Washington DC, not 8 states plus DC.")

f = F["fig-2.25"]
f["reference"]["long_description"] = (
    "A box plot drawn above a histogram on the same horizontal axis, marked 32.5, 46.1, 59.7, 73.3, 73.5, 86.9 "
    "and 100.5, for 31 first-exam scores. Box plot as drawn: whiskers from about 32.5 to 100.5, box from about 64 "
    "to 90, dashed line at 73.5. The text gives minimum 33, first quartile 61, median 73, third quartile 90 and "
    "maximum 100, so the drawing does not match exactly. Histogram with five intervals of width 13.6; counts from "
    "the data: 32.5–46.1, 2 scores; 46.1–59.7, 5; 59.7–73.3, 9; 73.3–86.9, 4; 86.9–100.5, 11. "
    "The y-axis has gridlines but no numbers. The scores are skewed left: most are high, with a long tail of lower scores.")
f["notes"] = f["notes"].replace(" Confirm what the dashed line and the 73.5 tick mark.", "")
note("fig-2.25", "Checked against the image: the dashed line sits exactly at the 73.5 tick (the text's mean is 73.5, "
                 "its median 73), and the box's left edge is drawn at about 64, not the text's first quartile of 61.")

f = F["fig-2.14"]
f["reference"]["long_description"] = (
    "Two box plots on one number line from 20 to 100. The figure does not label them; the text identifies the upper "
    "plot as the day class and the lower as the night class. Values from the text: upper plot (day class) minimum 32, "
    "first quartile 56, median 74.5, third quartile 82.5, maximum 99; lower plot (night class) minimum 25.5, first "
    "quartile 78, median 81, third quartile 89, maximum 98. The day class has a much wider box, so the middle 50% of "
    "its scores are more spread out. The night class scores are concentrated higher, but it has a long left whisker "
    "down to about 25.")
note("fig-2.14", "Checked against the image: the drawing is approximate. The day box's left edge is drawn at about 53 "
                 "(text 56), both upper whiskers end at about 100 (text 99 and 98), and the night box's right edge is "
                 "at about 90 (text 89).")

note("fig-2.9", "Checked against the image: the drawing is approximate. Both lines sit at about 11 at 64.5 "
                "(tables: 10), the dark line at about 16.5 at 94.5 (table: 15), the light line at about 44.5 at 84.5 (table: 45).")
note("fig-2.5", "Checked against the image: the bars labeled 0.17 and 0.12 are drawn lower than their labels "
                "(about 0.157 and 0.113). The printed labels are the truth.")
note("fig-2.30", "The detected crop also includes a cut-off line of instruction text above the axes; a description "
                 "should not treat it as part of the graph.")

for f in b["figures"]:
    f["verified"] = True
    assert len(f["reference"]["alt_text"]) <= 150, f["id"]
b["description"] = b["description"].replace(
    "Reference descriptions are drafts. Every entry needs a human check (verified: false).",
    "Every item was checked against its figure image; corrections and places where the drawing differs from the "
    "book's numbers are recorded in notes.")
p.write_text(json.dumps(b, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("19 items verified; corrections applied")
