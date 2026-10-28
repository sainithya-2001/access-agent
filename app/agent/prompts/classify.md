You are helping a university disability services office make course materials accessible.
Classify the image region shown. It comes from a textbook page.

Caption near the region: {caption}
Text near the region on the page:
"""
{nearby_text}
"""

Types:
- chart: any data graphic (bar, line, histogram, scatter, pie, box plot, stem-and-leaf, dot plot)
- diagram: process, structure, flowchart, labeled illustration, map
- photo: photograph
- equation: mathematical expression shown as an image
- table_image: a table that is an image rather than text
- text_image: an image that is mostly text (a screenshot, a sign)
- decorative: adds no information (ornament, background, stock photo unrelated to the text)
- other

Return ONLY JSON:
{"type": "<one type>", "is_decorative": true|false, "reason": "<one short sentence>"}
