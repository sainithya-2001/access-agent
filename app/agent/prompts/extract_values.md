Read this figure carefully and report exactly what is visible. Do not describe or interpret it.
This output is used to fact-check a description written separately, so precision matters
more than completeness. Never guess a label you cannot read.

Figure type: {type}

Report:
- title: the figure's title if one is shown, else null
- x_axis_label, y_axis_label: axis titles if shown, else null
- labels: every visible text label (tick labels, legend entries, category names, annotations)
- data: for charts, one entry per data point / bar / category: {"label": ..., "value": number, "series": ... or null}
  If values are printed on the figure, use them exactly.
  If not, estimate each value from the axis scale as precisely as you can and set values_estimated to true.
- values_estimated: true if any value was read off the axis rather than printed
- trend: one sentence on the overall pattern, with the key numbers (e.g. "rises from about 184 to about 230, with a small dip at year 7")
- labels_cut_off: true if axis labels, a legend, or part of the figure appear cut off at the edge of the image
- series_identified_by: how a reader can tell the data series apart:
  "single_series" (only one series), "legend" (a legend names each series), "direct_labels"
  (series names written on or next to the lines/bars), "color_only" (two or more series that differ
  only by colour, with no legend or labels naming them), or "other"
- x_axis_numeric_scale, y_axis_numeric_scale: true if that axis has numeric tick labels (numbers
  printed along it), false if it has no numbers (e.g. gridlines or tick marks only, or only category
  names), null if the figure has no such axis
- value_axis: the axis the data values are measured on: "y" for vertical bars, histograms and
  line charts; "x" for horizontal bars and horizontal box plots; null if not a chart

Return ONLY JSON with exactly these keys:
{"title": ..., "x_axis_label": ..., "y_axis_label": ..., "labels": [...], "data": [...],
 "values_estimated": ..., "trend": ..., "labels_cut_off": ..., "series_identified_by": ...,
 "x_axis_numeric_scale": ..., "y_axis_numeric_scale": ..., "value_axis": ...}
