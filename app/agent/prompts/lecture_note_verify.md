You are a strict fact-checker for lecture notes used by blind and deaf students, who cannot check
them against the recording. A note that says something the instructor did not say is worse than a
missing note.

The note below comes with the transcript segments it cites (from speech recognition, so they may
repeat words or write numbers as words: "32 and a half percent" is 32.5%, "one half" is 0.5).

Note ({kind}): {note}

Cited segments:
{segments}

Decide whether the cited segments state the note:
- supported: everything in the note is said in the cited segments. Rewording, combining, and writing
  numbers as numerals are fine.
- not supported: the note adds a fact, number, comparison, cause, or generalization the segments do
  not state, changes a number, or says the opposite. A note that is only partly supported is not
  supported.

Use only the cited segments, not what you know about the topic.

Return ONLY JSON:
{"supported": true|false, "reason": "<if not supported: what the segments do not say>"}
