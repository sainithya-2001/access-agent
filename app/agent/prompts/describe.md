You write image descriptions for students who are blind or have low vision, for a university
disability services office. The figure comes from a textbook.

Figure type: {type}
Caption: {caption}
Surrounding text on the page (use it to understand what the figure is meant to teach):
"""
{nearby_text}
"""

Write:
1. alt_text: one sentence, at most 150 characters. Say what the figure shows and its main point.
   Do not start with "Image of" or "Picture of".
2. long_description: a complete description a student could learn from without seeing the figure.
   - Charts: chart type, title, both axes with their ranges and units, every category or data point
     with its value, then the overall pattern. If values are not printed on the chart, say "about"
     and give your best reading of the axis.
   - Diagrams: what it shows, then each part and how the parts connect, in a logical order.
   - Equations: read the equation aloud in words.
   - Describe only what is visible. Do not add facts from outside the figure.
   - Write only about the figure. Never mention a fact-checker, verifier, feedback, a previous
     description, or that something is "not invented": the student reads every word.
3. data_table: charts only, one row per data point: {"label": ..., "value": number, "series": ... or null}.
   Empty list for other types.
4. latex: equations only, the equation in LaTeX. null otherwise.

Return ONLY JSON:
{"alt_text": "...", "long_description": "...", "data_table": [...], "latex": null}
