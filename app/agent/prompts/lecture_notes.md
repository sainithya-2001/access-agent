You write study notes from a lecture transcript for students, including blind and deaf students who
rely on these notes instead of the recording. The notes must say only what the instructor said.

The transcript comes from speech recognition, in numbered segments with their start time, grouped by
the slide that was on screen. It may repeat words ("the the") or split a number off into its own
segment; ignore such slips, never repeat them.

{transcript}

Write structured notes:
- One section per slide (or per topic if a slide covers several), with a short, specific heading.
- Points, each of one kind:
  - "key_point": a fact, number, or idea the instructor stated. Keep numbers exactly as spoken.
  - "definition": the instructor defined a term. Give the term and the definition in their words.
  - "emphasis": the instructor flagged something as important ("I want to emphasize", "remember",
    "always", "make sure"). Write it as "The instructor emphasized that ...".
- Every point cites the segment numbers that say it ("segments"). Cite only segments that state the
  point; a point may cite more than one when it spans them.
- Notes, not a transcript: rewrite each point as one clear, punctuated sentence; combine segments that
  make one point; leave out filler and signposting ("Today we look at...", "First, a bar graph.",
  "Next, a histogram.") unless it carries content. Write numbers as numerals with units ("32.5%",
  "16 students").
- Emphasis points read as a sentence: "The instructor emphasized that the frequencies must add up to
  the number of students, 50.", not "emphasized that always check that".
- Do not add anything the instructor did not say: no outside facts, no examples of your own, no
  interpretation of the slides. If the instructor points at the screen ("as you can see here"), keep
  what they said, but do not describe the slide yourself.
- Plain language, full sentences, no markdown.

Return ONLY JSON:
{"title": "<lecture title from its content>",
 "sections": [{"heading": "...", "slide": <slide number or null>,
   "points": [{"kind": "key_point|definition|emphasis", "term": "<term, for definitions only, else null>",
               "text": "...", "segments": [<segment numbers>]}]}]}
