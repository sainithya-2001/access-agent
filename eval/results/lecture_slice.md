# Lecture slice: test clip results and cost

Test clip: `data/lectures/test_lecture.mp4` (`scripts/make_test_lecture.py`), 81.4 s, two slides (PDF
pages 85 and 90: Figures 2.3 and 2.6), narrated by Windows SAPI (Microsoft David). Ground truth:
`data/lectures/test_lecture.json`. Checks: `scripts/test_lecture.py` (model calls blocked, cache
only) and `scripts/test_portal.py` (model APIs on a closed port). Run 2026-10-30, Meta backend.

| Step | What it found on the clip | Uncached calls | Cost |
|---|---|---|---|
| 1 Test clip | 81.4 s, 761 KB; slide change at 39.45 s | 0 (local TTS, ffmpeg) | $0 |
| 2 Transcription | `POST /v1/asr/transcribe` (file endpoint, ENDPOINTING) worked; 13 turns; "As you can see here" at 10.70 s (script estimate 10.6 s); ASR artifacts kept (doubled words, a stray "50" turn) | 1 Muse Voice Transcribe (81.44 s audio) | $0.0040 |
| 3 Slides + figures | 1 change detected at 39.5 s (changed-pixel share 6.6% vs 0 elsewhere); segments 0–5 / 6–12 linked; slide figures through the figure pipeline (page check on image-only pages): Figure 2.3 auto-approved, Figure 2.6 needs review (SAM could not confirm Spark's box) | 13 Muse Spark + 9 SAM 3.1 images | $0.120 + $0.023 = $0.142 |
| 4 Notes | 2 sections, 9 points (first prompt); every point cites segments | 2 Muse Spark (first prompt, tightened prompt) | $0.044 |
| 5 Notes verifier | one claim per point (prompt change): 16 of 17 supported, 1 flagged ("bar graph" said one segment earlier than cited); planted errors 3/3 caught; 1 visual reference → slide 1's verified figure | 1 Muse Spark (notes rewrite) + 4 verify | $0.030 + $0.072 = $0.103 |
| 6 Notes page + captions | 20 caption cues (≤ 84 chars per line); axe 0 in every profile, Blindfold, zoom, read aloud, Hear the chart | 0 | $0 |
| 7 Portal + inbox | watcher waits for a file to stop growing, then processes it; "New materials ready (1)"; video seeks; axe 0 | 0 (cached) | $0 |
| **Total** | | **30 uncached** (224 calls in all, the rest cached) | **$0.29** |

Prices: Muse Spark 1.1 $1.25 / $4.25 per million input / output tokens; SAM 3.1 $2.50 per 1,000 images;
Muse Voice Transcribe $0.18 per hour of audio ($0.003 per minute, billed in whole seconds). From
dev.meta.ai/docs/pricing-rate-limits, 2026-10-30.

**One new lecture like this clip, without the development reruns:** transcription $0.004 + slide
figures $0.142 + notes $0.030 + one verify call ≈ $0.018 ≈ **$0.20 for 81 s**. Transcription alone is
$0.003 per minute; the cost grows with the number of slide figures (≈ $0.07 each here), not with the
length of the talk.

Seen while testing:
- The notes verifier is strict as intended (a partly supported point fails), and its verdict on a
  borderline point can change with the other points in the same call: the flagged point passed when
  three planted errors were checked alongside it.
- Slide frames are image-only pages, so the figure pipeline needs the page check (Muse Spark + SAM),
  crops include the printed caption (no caption text box to stop at), and printed tables can't
  supply exact values.

## Update 2026-10-30: one verifier call per point

The notes verifier now checks each point in its own call, with only that point and its cited segments
(`notes.verify_point`). Before, one batched call checked all points, and the borderline point ("This
bar graph shows ...") passed when the three planted errors were in the same call but failed without
them. Now: 16 of 17 genuine points supported, the same point flagged (its cited segment never says "bar
graph"), and every genuine point's verdict is identical with and without the plants; planted errors
still 3/3 (the changed number by the code check with no call, the other two by their own calls).
Cost: 19 uncached calls (17 points + 2 plants), 5,384 input / 14,178 output tokens, $0.067, about
$0.0035 per point; a lecture like the clip now spends ~$0.06 on verification instead of ~$0.02.

With the slides PDF (`data/lectures/test_lecture.pdf`), both slides match their pages (0.994, 0.995)
and the slide figures come from the real pages at $0 (cached from the textbook runs): Figure 2.3 with
Table 2.9's exact values, Figure 2.6 auto-approved.
