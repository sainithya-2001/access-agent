"""Speech to text with Muse Voice Transcribe (Meta Model API, file endpoint).

  from app.transcribe import transcribe
  t = transcribe("data/lectures/test_lecture.mp4")     # Transcript: timestamped segments (turns)

The audio track is converted with ffmpeg (imageio-ffmpeg) to what the endpoint takes: mono 16-bit PCM
WAV at 24 kHz. POST /v1/asr/transcribe, multipart: `request` (JSON settings) and `audio` (the WAV);
mode ENDPOINTING, so the model finds turn boundaries and returns `turns` with startMs / endMs.
Recordings over 10 minutes go in 9-minute chunks, offsets added back. Each chunk's response is cached
by the hash of its audio and settings (re-running costs $0) and logged to output/llm_calls.jsonl with
its audio seconds and cost. Turn-level timestamps only: the API has no word timestamps or confidence.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg
from dotenv import load_dotenv

from app import cache
from app.llm import _log, check_allowed
from app.models import Transcript, TranscriptSegment

load_dotenv()

MODEL = os.environ.get("TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
URL = os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1").rstrip("/") + "/asr/transcribe"
RATE = 24000
CHUNK_S = 9 * 60          # under the 10-minute limit
USD_PER_HOUR = 0.18       # Meta pricing page, Oct 2026; billed in whole seconds
SETTINGS = {"model": MODEL, "audioEncoding": "WAV", "mode": "ENDPOINTING"}


def audio_pcm(path: str | Path) -> bytes:
    """The recording's audio as raw mono 16-bit PCM at 24 kHz."""
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(path), "-vn",
                          "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-c:a", "pcm_s16le", "-"],
                         check=True, capture_output=True)
    return out.stdout


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    return buf.getvalue()


def cost_usd(seconds: float) -> float:
    return int(seconds) * USD_PER_HOUR / 3600


def _post(wav: bytes) -> dict:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set in .env (needed for Muse Voice Transcribe).")
    b = uuid.uuid4().hex
    body = b"".join([
        f'--{b}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n'.encode(),
        json.dumps(SETTINGS).encode(), b"\r\n",
        f'--{b}\r\nContent-Disposition: form-data; name="audio"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
        wav, b"\r\n", f"--{b}--\r\n".encode()])
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={b}",
        "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"Transcription failed: HTTP {e.code} {detail}") from None


def _chunk(pcm: bytes, step: str) -> dict:
    wav = wav_bytes(pcm)
    k = cache.make_key(MODEL, step, SETTINGS, extra=wav)
    seconds = len(pcm) / 2 / RATE
    if (hit := cache.get(k)) is not None:
        _log({"step": step, "backend": "meta", "model": MODEL, "cached": True, "key": k, "audio_seconds": round(seconds, 2)})
        return json.loads(hit)
    check_allowed(step)
    start = time.time()
    resp = _post(wav)
    spent = {"audio_seconds": round(resp.get("audioDurationMs", seconds * 1000) / 1000, 2),
             "usd": round(cost_usd(resp.get("audioDurationMs", seconds * 1000) / 1000), 6),
             "seconds": round(time.time() - start, 2)}
    _log({"step": step, "backend": "meta", "model": MODEL, "cached": False, "key": k,
          "request_id": resp.get("sessionId"), **spent})
    cache.put(k, json.dumps(resp), spent)
    return resp


def transcribe(path: str | Path) -> Transcript:
    pcm = audio_pcm(path)
    per_chunk = CHUNK_S * RATE * 2
    segments, texts, total = [], [], 0.0
    for n, i in enumerate(range(0, len(pcm), per_chunk)):
        offset = i / 2 / RATE
        resp = _chunk(pcm[i:i + per_chunk], f"transcribe:{n}")
        texts.append(resp.get("transcript", "").strip())
        total += resp.get("audioDurationMs", 0) / 1000
        for t in resp.get("turns", []):
            if t.get("transcript", "").strip():
                segments.append(TranscriptSegment(id=len(segments), start=round(offset + t["startMs"] / 1000, 3),
                                                  end=round(offset + t["endMs"] / 1000, 3), text=t["transcript"].strip()))
    return Transcript(source=str(path), model=MODEL, duration=round(total, 3), segments=segments,
                      text=" ".join(t for t in texts if t))
