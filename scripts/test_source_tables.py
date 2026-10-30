"""Source-table values (app/source_tables.py): synthetic cases, no model calls.

Run: python scripts/test_source_tables.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app import source_tables  # noqa: E402
from app.agent import tools  # noqa: E402
from app.models import (BBox, Classification, DataPoint, Description, ExtractedValues, FigureResult,  # noqa: E402
                        Region, RegionType)

BOX = BBox(x0=0, y0=0, x1=100, y1=100)


def table(rid: str, page: int, caption: str, rows) -> Region:
    return Region(id=rid, page=page, kind="table", bbox=BOX, crop_path="", source="table", caption=caption,
                  table_rows=rows)


def chart(page: int, points, long="A bar chart titled Proportion (%) of TikTok Users, by Age Groups. "
                                   "The bars fall from about 32% to about 7%.", **ex) -> FigureResult:
    data = [DataPoint(label=l, value=v, series=s) for l, v, *rest in points for s in [rest[0] if rest else None]]
    ex.setdefault("values_estimated", True)
    ex.setdefault("series_identified_by", "single_series")
    ex.setdefault("y_axis_numeric_scale", True)
    ex.setdefault("value_axis", "y")
    return FigureResult(
        region=Region(id=f"p{page}_f0", page=page, bbox=BOX, crop_path="", source="embedded", caption="Figure 2.3",
                      nearby_text="Figure 2.3 Table 2.9"),
        classification=Classification(type=RegionType.chart),
        extracted=ExtractedValues(title="Proportion (%) of TikTok Users", x_axis_label="Age Groups",
                                  y_axis_label="Proportion (%) of TikTok Users", labels=[p[0] for p in points],
                                  data=data, **ex),
        description=Description(alt_text="Bar chart of TikTok users by age group.", long_description=long,
                                data_table=[d.model_copy() for d in data]))


# Table 2.9, split over PDF pages 84 and 85 (header repeated), labels with en dashes.
T29 = [table("p84_t1", 84, "Table 2.9", [["Age groups", "Proportion (%) of TikTok users"],
                                         ["10–19", "32.5%"], ["20–29", "29.5%"], ["30–39", "16.4%"]]),
       table("p85_t1", 85, "Table 2.9", [["Age groups", "Proportion (%) of TikTok users"],
                                         ["40–49", "13.9%"], ["50+", "7.1%"]])]
READ = [("10-19", 32.5), ("20-29", 29.3), ("30-39", 16.5), ("40-49", 14.0), ("50+", 7.0)]

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))


# 1. Every reading matches (split table, dashes): exact values, sentence, source, verifier accepts.
res = chart(85, READ)
m, why = source_tables.match(res, T29)
check("Figure 2.3: Table 2.9 matched across pages 84–85", m is not None and m.table.pages == [84, 85], str(why))
applied, summary = source_tables.apply(res, m)
check("applied", applied, summary)
check("data = the table's exact values", [d.value for d in res.extracted.data] == [32.5, 29.5, 16.4, 13.9, 7.1],
      str([d.value for d in res.extracted.data]))
check("readings kept", [d.value for d in res.extracted.source.readings] == [v for _, v in READ])
check("source named", res.extracted.source.caption == "Table 2.9")
check("description quotes the table", res.description.long_description.endswith(
    "Values from Table 2.9: 10–19, 32.5%; 20–29, 29.5%; 30–39, 16.4%; 40–49, 13.9%; 50+, 7.1%."),
    res.description.long_description)
issues = tools.code_checks(res.description, res.extracted, "Figure 2.3")  # page text without "Table 2.9"
check("verifier accepts the attributed values", not issues, "; ".join(i.detail for i in issues))
check("verify prompt unchanged (source left out)", "source" not in res.extracted.model_dump_json(exclude={"source"}))

# 1b. The description's own approximate number, near the reading (29.3) but not the table (29.5,
#     tolerance ±1.02), still passes: it was written from the drawing and checked against the reading.
res = chart(85, READ, long="A bar chart titled Proportion (%) of TikTok Users, by Age Groups. "
                           "The 20-29 bar is about 28.4%.")
applied, summary = source_tables.apply(res, source_tables.match(res, T29)[0])
check("description number near the reading accepted", applied, summary)

# 2. One point outside the tolerance → keep the readings.
res = chart(85, READ[:4] + [("50+", 12.0)])
m, why = source_tables.match(res, T29)
check("one point off → no match", m is None and why and "not every value" in why, str(why))

# 3. Two columns fit equally well, neither named like the chart → ambiguous, no match.
twin = [table("p85_t3", 85, "Table 9.9", [["Age", "A", "B"], ["10-19", "32.5", "32.4"], ["20-29", "29.5", "29.6"],
                                          ["30-39", "16.4", "16.3"], ["40-49", "13.9", "14.0"], ["50+", "7.1", "7.0"]])]
m, why = source_tables.match(chart(85, READ), twin)
check("two equal columns → no match", m is None and why and "equally" in why, str(why))

# 4. Two columns fit, one named like the chart's title/axis → that one.
named = [table("p85_t3", 85, "Table 9.9", [["Age", "Other", "Proportion"], ["10-19", "32.5", "32.4"],
                                           ["20-29", "29.5", "29.6"], ["30-39", "16.4", "16.3"],
                                           ["40-49", "13.9", "14.0"], ["50+", "7.1", "7.0"]])]
m, why = source_tables.match(chart(85, READ), named)
check("the column named like the chart wins", m is not None and m.by_series[None].name == "Proportion", str(why))

# 5. Too far away (two pages) → not used.
m, _ = source_tables.match(chart(87, READ), T29)
check("table two pages away ignored", m is None)

# 6. No numeric scale → readings are guesses: never matched.
m, _ = source_tables.match(chart(85, READ, y_axis_numeric_scale=False), T29)
check("no numeric scale → not matched", m is None)

# 7. Two series, matched by name to the table's columns, by label "District".
t212 = [table("p86_t2", 86, "Table 2.12", [["District", "Registered voter population", "Overall city population"],
                                           ["1", "15.5%", "19.4%"], ["2", "12.2%", "15.6%"], ["3", "9.8%", "9.0%"]])]
pts = [("1", 15.3, "Registered voters"), ("2", 12.0, "Registered voters"), ("3", 9.9, "Registered voters"),
       ("1", 19.2, "City population"), ("2", 15.5, "City population"), ("3", 9.1, "City population")]
res = chart(86, pts, long="Grouped bars for six districts.", series_identified_by="legend")
m, why = source_tables.match(res, t212)
check("two series matched by name", m is not None and m.by_series["City population"].name == "Overall city population",
      str(why))
if m:
    source_tables.apply(res, m)
    check("two series: exact values", [d.value for d in res.extracted.data] == [15.5, 12.2, 9.8, 19.4, 15.6, 9.0])
    check("two series: sentence per series", "Registered voters: District 1, 15.5%" in res.description.long_description,
          res.description.long_description)

# 8. A transposed table (labels across the header row) works too.
wide = [table("p85_t4", 85, "Table 8.8", [["Age group", "10-19", "20-29", "30-39", "40-49", "50+"],
                                          ["Share (%)", "32.5", "29.5", "16.4", "13.9", "7.1"]])]
m, why = source_tables.match(chart(85, READ), wide)
check("transposed table matched", m is not None, str(why))

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
