"""Lecture slice: one recording through transcription, slide detection and linking, and the figure
pipeline for its slides (app/lecture.py).

  python scripts/run_lecture.py data/lectures/test_lecture.mp4

Writes output/lectures/<id>/ (lecture.json, slides/, slides.pdf, frame_diffs.json); the slides'
figures go into the review store (output/review.db) as their own document. Cached: re-runs cost $0.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app import pipeline, store  # noqa: E402
from app.lecture import process_lecture  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--title")
    args = ap.parse_args()
    start = pipeline.log_offset()
    lec = process_lecture(args.video, store.connect(), args.title, echo=True)
    print(f"\n{lec.title}: {lec.transcript.duration:.1f} s, {len(lec.transcript.segments)} segments, {len(lec.slides)} slides")
    for s in lec.slides:
        print(f"  slide {s.index}: {s.start:.1f}-{s.end:.1f} s, segments {s.segment_ids}")
    calls = pipeline.calls_since(start)
    print(f"Model calls: {len(calls)} ({sum(not c.get('cached') for c in calls)} uncached)")


if __name__ == "__main__":
    main()
