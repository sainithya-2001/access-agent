"""Make the lecture slice's test clip: two slides (PDF pages 85 and 90: Figures 2.3 and 2.6) shown in
sequence with narration spoken by a local Windows voice.

  python scripts/make_test_lecture.py [--voice "Microsoft David Desktop"]

Writes data/lectures/test_lecture.mp4 (1280x720, 5 fps, H.264 + AAC) and data/lectures/test_lecture.json,
the ground truth for the lecture tests: each slide's PDF page, start and end time, and narration text,
plus the time of the "as you can see here" moment. Speech: System.Speech (SAPI) through PowerShell,
24 kHz mono 16-bit. Video: the ffmpeg bundled with imageio-ffmpeg. No model calls.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))
from scripts.run_ingest import pick_pdf  # noqa: E402

OUT = ROOT / "data" / "lectures"
RATE = 24000
GAP = 0.8      # seconds of silence before each slide's narration
SIZE = (1280, 720)

# Only values printed on the pages (Figure 2.3 / Table 2.9; Example 2.8's data for Figure 2.6).
SLIDES = [
    {"page": 85, "text": (
        "Today we look at two ways to display data. First, a bar graph. This one shows the proportion "
        "of TikTok users in each age group. As you can see here, the tallest bar is the ten to nineteen "
        "age group, at about thirty-two and a half percent, followed by twenty to twenty-nine, at "
        "twenty-nine and a half percent. After that the bars drop: about sixteen percent for thirty to "
        "thirty-nine, about fourteen percent for forty to forty-nine, and seven percent for fifty and "
        "older. In a bar graph the bars stand for categories, so they are separated by gaps.")},
    {"page": 90, "text": (
        "Next, a histogram. A histogram is a graph of quantitative data in which the bars touch, and "
        "each bar covers an interval of values. Here the data are the number of books bought by fifty "
        "part-time students at ABC College. The intervals run from one half to six and a half, one book "
        "wide, so there are six bars. Three books is the most common answer, with sixteen students. "
        "Eleven students bought one book, and ten bought two. I want to emphasize this: the frequencies "
        "must add up to the number of students, fifty. Always check that before you draw the histogram.")},
]
SEE_HERE = "As you can see here"

TTS_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($args[0])
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(%d, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($args[2], $fmt)
$s.Speak([System.IO.File]::ReadAllText($args[1], [System.Text.Encoding]::UTF8))
$s.Dispose()
""" % RATE


def speak(text: str, voice: str, wav: Path, tmp: Path) -> None:
    txt, ps = tmp / f"{wav.stem}.txt", tmp / "tts.ps1"
    txt.write_text(text, encoding="utf-8")
    ps.write_text(TTS_PS, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps), voice, str(txt), str(wav)],
                   check=True)


def frames(wav: Path) -> bytes:
    with wave.open(str(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2), wav
        return w.readframes(w.getnframes())


def slide_image(pdf: fitz.Document, page: int, path: Path) -> None:
    """The page, scaled to the frame height and centred on white."""
    pix = pdf[page - 1].get_pixmap(dpi=150)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.thumbnail(SIZE, Image.LANCZOS)
    canvas = Image.new("RGB", SIZE, "white")
    canvas.paste(img, ((SIZE[0] - img.width) // 2, (SIZE[1] - img.height) // 2))
    canvas.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Microsoft David Desktop")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    pdf = fitz.open(pick_pdf(None))
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        pcm, slides, concat = b"", [], []
        silence = b"\0\0" * int(GAP * RATE)
        for i, s in enumerate(SLIDES):
            wav = tmp / f"slide{i}.wav"
            speak(s["text"], args.voice, wav, tmp)
            start = len(pcm) / 2 / RATE
            pcm += silence + frames(wav)
            end = len(pcm) / 2 / RATE
            img = tmp / f"slide{i}.png"
            slide_image(pdf, s["page"], img)
            concat.append(f"file '{img.as_posix()}'\nduration {end - start:.3f}")
            slides.append({"index": i, "page": s["page"], "start": round(start, 3), "end": round(end, 3),
                           "speech_start": round(start + GAP, 3), "text": s["text"]})
        pcm += silence  # a little room at the end
        slides[-1]["end"] = round(len(pcm) / 2 / RATE, 3)
        audio = tmp / "narration.wav"
        with wave.open(str(audio), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
        concat.append(f"file '{(tmp / f'slide{len(SLIDES) - 1}.png').as_posix()}'")  # concat demuxer: last image again
        (tmp / "slides.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
        mp4 = OUT / "test_lecture.mp4"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", str(tmp / "slides.txt"), "-i", str(audio),
                        "-r", "5", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "64k", "-t", f"{len(pcm) / 2 / RATE:.3f}", "-movflags", "+faststart", str(mp4)], check=True)

    # Where "as you can see here" falls: words spread evenly over the slide's speech (an estimate).
    s0 = slides[0]
    words = s0["text"].split()
    k = len(s0["text"][:s0["text"].index(SEE_HERE)].split())
    spoken = s0["end"] - s0["speech_start"]
    truth = {"video": "data/lectures/test_lecture.mp4", "voice": args.voice, "duration": slides[-1]["end"],
             "slides": slides, "see_here": {"phrase": SEE_HERE, "slide": 0,
                                            "approx_time": round(s0["speech_start"] + spoken * k / len(words), 1)}}
    (OUT / "test_lecture.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"{mp4.relative_to(ROOT)}: {truth['duration']:.1f} s, {mp4.stat().st_size / 1024:.0f} KB; slides "
          + ", ".join(f"page {s['page']} {s['start']:.1f}-{s['end']:.1f} s" for s in slides))


if __name__ == "__main__":
    main()
