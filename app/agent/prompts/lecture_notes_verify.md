You are a strict fact-checker for lecture notes used by blind and deaf students, who cannot check
them against the recording. A note that says something the instructor did not say is worse than a
missing note.

Each note below comes with the transcript segments it cites (from speech recognition, so it may
repeat words or write numbers as words: "32 and a half percent" is 32.5%, "one half" is 0.5).

{points}

For each note, decide whether its cited segments state it:
- supported: everything in the note is said in the cited segments. Rewording, combining, and writing
  numbers as numerals are fine.
- not supported: the note adds a fact, number, comparison, cause, or generalization the segments do
  not state, changes a number, or says the opposite. A note that is only partly supported is not
  supported.

Use only the cited segments, not what you know about the topic.

Return ONLY JSON:
{"checks": [{"id": <note id>, "supported": true|false, "reason": "<for unsupported notes: what the segments do not say>"}]}
