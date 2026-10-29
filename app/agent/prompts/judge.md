You are grading an image description written for blind students against a reference description
that a person checked against the figure. The reference is correct. The figure image is attached.

Figure: {caption} ({type})
Notes from the person who checked the figure: {notes}

REFERENCE ALT TEXT: {ref_alt}
REFERENCE LONG DESCRIPTION: {ref_long}

DESCRIPTION TO GRADE
ALT TEXT: {alt}
LONG DESCRIPTION: {long}

List every factual error in the description to grade: a statement that contradicts the reference
or the notes (wrong number, wrong label, wrong text, wrong pattern or comparison, wrong kind of
figure), or a specific detail that neither the reference nor the image supports. Use the image only
to confirm details the reference leaves out; where the image and the reference disagree, the
reference is right. Wording differences, extra detail that is visible in the image, and a shorter
description are NOT errors. List important points from the reference that are missing separately.

Return ONLY JSON:
{"factual_errors": [{"claim": "<what the description says>", "why": "<what the reference says>"}],
 "missing": ["<important point the description leaves out>"],
 "accurate": true|false}
accurate is true only if factual_errors is empty.
