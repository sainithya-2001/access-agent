"""The demo lecture: a short lecture on bar graphs and histograms with natural synthetic narration
(Kokoro TTS, voice af_heart at speed 0.9, Apache 2.0, local CPU) over two OpenStax slides.

Writes data/portal/stats101/lecture2.mp4 (1280x720, 5 fps, H.264 + AAC), lecture2.pdf (the slides PDF:
textbook PDF pages 85 and 90, matched to the video by app/lecture.py), lecture2.json (the script with
each line's start and end, and when the slide changes) and course.json. Slide 1 (PDF page 85, Figure
2.3) is on screen until the line "Now compare that with a histogram", then slide 2 (PDF page 90,
Figure 2.6). No model calls.

Kokoro runs in its own venv so the app's environment (frozen at tag freeze-v1) is untouched:
  C:\\dev\\.venv-tts\\Scripts\\python -m pip install "kokoro>=0.9.4" soundfile
  python scripts/make_lecture2.py [--tts-python PATH] [--voice af_heart]
The script calls itself with --synthesize under that interpreter for the speech.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "portal" / "stats101"
RATE = 24000          # Kokoro's sample rate; app/transcribe.py resamples to 24 kHz anyway
SPEED = 0.9           # a little slower than Kokoro's default, for clarity
SENTENCE_GAP = 0.4    # seconds of silence between sentences
LINE_GAP = 0.9        # between lines (paragraphs) of the script
SLIDE_GAP = 1.2       # before the first line, at the slide change and at the end
PAGES = [85, 90]      # textbook PDF pages shown as slides 1 and 2
SWITCH = "Now compare that with a histogram"

SCRIPT = """Today we're looking at two ways to display data: bar graphs and histograms.
Let's start with a bar graph. This one shows the proportion of TikTok users in the United States by age group. The horizontal axis lists five age groups, and the vertical axis shows the percent of users, from zero to thirty-five percent.
As you can see here, the tallest bar is the youngest group, ages ten to nineteen, at thirty-two and a half percent. Ages twenty to twenty-nine are close behind at twenty-nine and a half percent. Then there's a big drop: ages thirty to thirty-nine are sixteen point four percent, forty to forty-nine are thirteen point nine percent, and fifty and over are just seven point one percent.
So the pattern is clear: the older the age group, the smaller its share of users. The two youngest groups together make up more than sixty percent.
One thing to remember about bar graphs: the bars are separate, because the categories are separate.
Now compare that with a histogram. This one shows how many books fifty part-time students bought for the semester. The horizontal axis is the number of books, and the vertical axis is frequency, meaning how many students.
Notice the bars touch each other. That's what makes it a histogram: the data are numbers on a scale, so the bars sit side by side. Each bar is centered on a whole number of books, with boundaries at the halves.
Eleven students bought one book, ten bought two, and the tallest bar is three books, with sixteen students. After that it falls off: six students bought four books, five bought five, and only two bought six.
So the most common number of books is three, and fewer students bought large numbers of books. The distribution has a longer tail on the right side.
To sum up: use a bar graph when your categories are separate groups, and a histogram when your data are numbers on a continuous scale. In both, the height of each bar tells you how many, or what share."""


def synthesize(voice: str, out_dir: Path) -> None:
    """Under the TTS venv: one WAV per script line (24 kHz mono 16-bit), sentence by sentence with
    SENTENCE_GAP between them."""
    import numpy as np
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a")  # American English
    pause = np.zeros(int(SENTENCE_GAP * RATE), np.float32)
    for i, line in enumerate(SCRIPT.splitlines()):
        parts = []
        for sent in re.split(r"(?<=[.!?])\s+", line.strip()):
            parts += [np.concatenate([np.asarray(a, dtype=np.float32) for _, _, a in pipe(sent, voice=voice, speed=SPEED)]), pause]
        audio = np.concatenate(parts[:-1])
        with wave.open(str(out_dir / f"line{i:02d}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def frames(wav: Path) -> bytes:
    with wave.open(str(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2), wav
        return w.readframes(w.getnframes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tts-python", default=r"C:\dev\.venv-tts\Scripts\python.exe")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--synthesize", metavar="DIR", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.synthesize:
        return synthesize(args.voice, Path(args.synthesize))

    import imageio_ffmpeg
    import pymupdf as fitz
    sys.path.insert(0, str(ROOT))
    from scripts.make_test_lecture import pick_pdf, slide_image

    OUT.mkdir(parents=True, exist_ok=True)
    lines = SCRIPT.splitlines()
    switch = next(i for i, l in enumerate(lines) if l.startswith(SWITCH))
    pdf = fitz.open(pick_pdf(None))
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        subprocess.run([args.tts_python, __file__, "--synthesize", str(tmp), "--voice", args.voice], check=True)
        pcm, timing = b"", []
        gap = lambda s: b"\0\0" * int(s * RATE)  # noqa: E731
        for i, line in enumerate(lines):
            pcm += gap(SLIDE_GAP if i in (0, switch) else LINE_GAP)
            start = len(pcm) / 2 / RATE
            pcm += frames(tmp / f"line{i:02d}.wav")
            timing.append({"line": i, "slide": 1 if i < switch else 2, "start": round(start, 3),
                           "end": round(len(pcm) / 2 / RATE, 3), "text": line})
        pcm += gap(SLIDE_GAP)
        duration = len(pcm) / 2 / RATE
        change = round(timing[switch]["start"] - SLIDE_GAP / 2, 3)  # mid-pause before the switch line
        audio = tmp / "narration.wav"
        with wave.open(str(audio), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
        imgs = []
        for k, page in enumerate(PAGES):
            imgs.append(tmp / f"slide{k}.png")
            slide_image(pdf, page, imgs[-1])
        # Each slide image looped for its own time, joined by the concat filter (the concat demuxer drops
        # the last image's duration in this ffmpeg build, ending the video track at the slide change).
        lengths = [change, duration - change]
        mp4 = OUT / "lecture2.mp4"
        inputs = [x for img, d in zip(imgs, lengths) for x in ("-loop", "1", "-framerate", "5", "-t", f"{d:.3f}", "-i", str(img))]
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs, "-i", str(audio),
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                        "-r", "5", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(mp4)],
                       check=True)

    deck = fitz.open()
    for p in PAGES:
        deck.insert_pdf(pdf, from_page=p - 1, to_page=p - 1)
    deck.save(OUT / "lecture2.pdf", garbage=4, deflate=True)
    (OUT / "lecture2.json").write_text(json.dumps({
        "video": "data/portal/stats101/lecture2.mp4", "voice": f"Kokoro {args.voice}, speed {SPEED}", "duration": round(duration, 3),
        "slides": [{"index": 1, "pdf_page": PAGES[0], "deck_page": 1, "start": 0.0, "end": change},
                   {"index": 2, "pdf_page": PAGES[1], "deck_page": 2, "start": change, "end": round(duration, 3)}],
        "lines": timing}, indent=2), encoding="utf-8")
    (OUT / "course.json").write_text(json.dumps({"title": "Introductory Statistics"}, indent=2), encoding="utf-8")
    print(f"{mp4.relative_to(ROOT)}: {duration:.1f} s, slide change at {change:.1f} s; "
          f"lecture2.pdf (pages {PAGES}); voice Kokoro {args.voice}")


if __name__ == "__main__":
    main()
