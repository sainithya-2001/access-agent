You are grading an image description written for blind students against a reference description
that a person checked against the figure. The reference is correct. The figure image is attached.

Figure: {caption} ({type})
Notes from the person who checked the figure: {notes}
Text around the figure on the page, which the description's author was given: {page_text}

REFERENCE ALT TEXT: {ref_alt}
REFERENCE LONG DESCRIPTION: {ref_long}

DESCRIPTION TO GRADE
ALT TEXT: {alt}
LONG DESCRIPTION: {long}

List every factual error in the description to grade: a statement that contradicts the reference
or the notes (wrong number, wrong label, wrong text, wrong pattern or comparison, wrong kind of
figure), or a specific detail that neither the reference nor the image supports. Use the image only
to confirm details the reference leaves out; where the image and the reference disagree, the
reference is right. Wording differences, extra detail that is visible in the image, a shorter
description, and a correct statement taken from the page text are NOT errors. A value read off an
axis and given as "about" is an error only if it is off by more than about 3% of the axis range;
a value printed on the figure must be exact. Comparisons ("the tallest bar", "about twice as tall")
must be right. List important points from the reference that are missing separately.

Return ONLY JSON:
{"factual_errors": [{"claim": "<what the description says>", "why": "<what the reference says>"}],
 "missing": ["<important point the description leaves out>"],
 "accurate": true|false}
accurate is true only if factual_errors is empty.
