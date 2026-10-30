"""Check the audio features without listening, for one PDF page of an export.

  - Blindfold: the exact utterances spoken on the page, in both versions, with rate and the pauses
    the code adds (a fake speech engine records them) → blindfold_p<N>_{original,accessible}.txt
  - Hear the chart: the figure's sonification rendered offline (OfflineAudioContext) → a WAV, then
    analysed: each tone's start, duration and frequency, pitch order vs. values, clicks before tones,
    series name spoken first, overlaps, clicks louder than tones, clipping → sonify_<fig>.txt
  - Reading profiles: a screenshot of the page in each profile → profile_<name>.png
  Then a zip of the transcripts and screenshots.

Usage:
  python scripts/listen_check.py                       # newest export, page 85, Figure 2.3
  python scripts/listen_check.py output/<doc>/accessible.html --page 85 --figure "Figure 2.3"
      [--values 32.5,29.5,16.4,13.9,7.1]               # also check pitch order against these values
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "screens"
sys.stdout.reconfigure(encoding="utf-8")
PROFILES = ["screen-reader", "low-vision", "reading", "keyboard"]
RATE = 44100

# Records every utterance and the time since the previous one ended (the pause the code adds; the
# time a real voice takes to say an utterance is not part of it). Each utterance "ends" at once.
FAKE_SPEECH = """
(() => {
  const log = [];
  let lastEnd = null, hold = () => false;
  window.__speech = { log, holdIf: (fn) => { hold = fn; } };
  class U { constructor(t) { this.text = t; this.rate = 1; this.lang = ""; } }
  window.SpeechSynthesisUtterance = U;
  const synth = {
    speaking: false, pending: false, paused: false,
    getVoices: () => [], cancel() {}, pause() {}, resume() {},
    speak(u) {
      const now = performance.now();
      const kind = document.querySelector("dialog.bf[open] .bf-kind");
      const pos = document.querySelector("dialog.bf[open] .bf-pos");
      log.push({ text: u.text, rate: u.rate, lang: u.lang, t: now,
                 pause: lastEnd === null ? null : now - lastEnd,
                 kind: kind ? kind.textContent : null, pos: pos ? pos.textContent : null });
      if (hold(log[log.length - 1])) return;  // stop here: never ends, so nothing follows
      setTimeout(() => { lastEnd = performance.now(); u.onend && u.onend({}); }, 0);
    },
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
})();
"""

# AudioContext → an OfflineAudioContext that logs what is scheduled. stop() with no time (the
# player's Stop/Finished) is ignored, so the scheduled tones are still there to render.
OFFLINE_AUDIO = """
(() => {
  const events = [];
  window.__audio = { events, ctx: null };
  const wrap = (node, type, ctx) => {
    const start = node.start.bind(node), stop = node.stop.bind(node);
    node.start = (at = 0) => {
      events.push({ type, at, freq: type === "tone" ? node.frequency.value : null, seq: window.__speech.log.length });
      start(at);
    };
    node.stop = (at) => { if (at !== undefined) stop(at); };
    return node;
  };
  class Offline extends OfflineAudioContext {
    constructor() { super({ numberOfChannels: 1, length: 44100 * 30, sampleRate: 44100 }); window.__audio.ctx = this; }
    get state() { return "running"; }
    resume() { return Promise.resolve(); }
    createOscillator() { return wrap(super.createOscillator(), "tone", this); }
    createBufferSource() { return wrap(super.createBufferSource(), "click", this); }
  }
  window.AudioContext = Offline;
  window.webkitAudioContext = Offline;
})();
"""


def newest_export() -> Path:
    return max((ROOT / "output").glob("*/accessible.html"), key=lambda p: p.stat().st_mtime)


def figure_id(page, caption: str) -> str:
    fid = page.evaluate("""(cap) => {
        const f = [...document.querySelectorAll(".figure-block")]
          .find((b) => b.querySelector("figcaption")?.textContent.trim() === cap);
        return f ? f.id : null; }""", caption)
    if not fid:
        sys.exit(f"{caption} not found in the export")
    return fid


# ---------- Blindfold ----------
def blindfold(browser, url: str, pdf_page: int, caption: str, version: str) -> tuple[str, int]:
    ctx = browser.new_context()
    ctx.add_init_script(f"try {{ localStorage.setItem('blindfold-version', '{version}'); }} catch (e) {{}}")
    ctx.add_init_script(FAKE_SPEECH)
    page = ctx.new_page()
    page.goto(url)
    rate = page.evaluate("(() => { try { return localStorage.getItem('blindfold-rate'); } catch (e) { return null; } })()")
    # Hold at the first utterance past the page, then stop.
    page.evaluate("""(n) => window.__speech.holdIf((e) => {
        const m = /PDF page (\\d+)/.exec(e.pos || ""); return m && Number(m[1]) > n; })""", pdf_page)
    page.evaluate("(n) => Blindfold.open(Blindfold.fromDocument(document), document.getElementById('blindfold-open'), { page: n })",
                  pdf_page)
    page.wait_for_function("""(n) => window.__speech.log.some((e) => {
        const m = /PDF page (\\d+)/.exec(e.pos || ""); return m && Number(m[1]) > n; })""", arg=pdf_page, timeout=30000)
    log = page.evaluate("window.__speech.log")
    ctx.close()

    on_page = [e for e in log if re.search(rf"PDF page {pdf_page}\b", e["pos"] or "")]
    name = "Original PDF" if version == "original" else "Accessible"
    lines = [
        f"Blindfold transcript: {name} version, PDF page {pdf_page} ({caption})",
        f"Source: {Path(url[8:]).relative_to(ROOT).as_posix() if url.startswith('file:///') else url}",
        f"Speech rate: {on_page[0]['rate'] if on_page else '?'}× (the default; a saved choice would be "
        f"'blindfold-rate' = {rate})",
        f"Voice: the browser's default voice for lang \"{on_page[0]['lang'] if on_page else '?'}\"",
        "Pauses: the silence the code adds before an utterance. A new block waits 50 ms; the pieces of",
        "one block (split at sentence ends, at most 220 characters each) follow at once. How long the",
        "voice takes to say each utterance is not included; it depends on the voice.",
        f"Utterances: {len(on_page)} in {len({e['pos'] for e in on_page})} blocks",
        "",
    ]
    last_pos = None
    for k, e in enumerate(on_page, 1):
        if e["pos"] != last_pos:
            block = re.search(r"block (\d+ of \d+)", e["pos"]).group(1)
            lines.append(f"[block {block} · {e['kind']}]")
            last_pos = e["pos"]
        pause = "first" if k == 1 else f"{round(e['pause'] / 10) * 10:.0f} ms"
        lines.append(f"  {k:3d}. pause {pause:>6} · rate {e['rate']}× · \"{e['text']}\"")
    path = OUT / f"blindfold_p{pdf_page}_{version}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.name, len(on_page)


# ---------- Hear the chart ----------
def render_sonification(browser, url: str, caption: str) -> tuple[np.ndarray, dict, list, list]:
    ctx = browser.new_context()
    ctx.add_init_script(FAKE_SPEECH)
    ctx.add_init_script(OFFLINE_AUDIO)
    page = ctx.new_page()
    page.goto(url)
    fid = figure_id(page, caption)
    btn = page.locator(f"#{fid} .hear-chart-btn")
    if not btn.count():
        sys.exit(f"{caption} has no Hear the chart button")
    sound = json.loads(btn.get_attribute("data-sound"))
    btn.click()
    page.wait_for_function(f"document.querySelector('#{fid} .hear-chart-status').textContent === 'Finished.'",
                           timeout=60000)
    samples = page.evaluate("""async () => {
        const buf = await window.__audio.ctx.startRendering();
        return Array.from(buf.getChannelData(0)); }""")
    events = page.evaluate("window.__audio.events")
    speech = page.evaluate("window.__speech.log.map((e) => e.text)")
    ctx.close()
    x = np.asarray(samples, dtype=np.float64)
    loud = np.nonzero(np.abs(x) > 1e-4)[0]
    x = x[: (loud[-1] + int(0.25 * RATE)) if loud.size else RATE]  # trim the silent tail
    return x, sound, events, speech


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def segments(x: np.ndarray, thresh: float = 2e-3, gap: float = 0.015) -> list[tuple[int, int]]:
    """Runs of sound above the threshold, merged across gaps shorter than `gap` seconds."""
    idx = np.nonzero(np.abs(x) > thresh)[0]
    if not idx.size:
        return []
    breaks = np.nonzero(np.diff(idx) > gap * RATE)[0]
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]] + 1
    return list(zip(starts, ends))


def peak_freq(seg: np.ndarray) -> float:
    mid = seg[len(seg) // 8: -len(seg) // 8]  # skip the attack and release ramps
    spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid)), n=1 << 19))
    k = int(np.argmax(spec[1:])) + 1
    a, b, c = np.log(spec[k - 1: k + 2] + 1e-12)
    k += 0.5 * (a - c) / (a - 2 * b + c)  # parabolic interpolation
    return k * RATE / (1 << 19)


def rank(v) -> list[int]:
    return list(np.argsort(np.argsort(-np.asarray(v), kind="stable"), kind="stable"))


def analyse(x: np.ndarray, sound: dict, events: list, speech: list, values: list[float] | None) -> tuple[list[str], list[str]]:
    lines, problems = [], []
    points = [p for s in sound["series"] for p in s["points"]]
    lo = 0 if sound["from_zero"] else sound["min"]
    expected = [220 * 4 ** max(0, min(1, (p["value"] - lo) / (sound["max"] - lo))) for p in points]

    segs = segments(x)
    clicks = [(a, b) for a, b in segs if (b - a) / RATE < 0.04]
    tones = [(a, b) for a, b in segs if (b - a) / RATE >= 0.04]
    lines.append(f"Rendered {len(x) / RATE:.2f} s at {RATE} Hz, mono. Found {len(tones)} tones and {len(clicks)} clicks "
                 f"(sound above {2e-3} for ≥ 40 ms = tone, shorter = click).")
    lines.append("Offline time starts when the tones are scheduled, i.e. after the series name has been spoken.")
    lines.append("")
    lines.append(" #  label   value  click at   tone at   duration  freq (Hz)  expected  peak    RMS")
    tone_stats = []
    for i, (a, b) in enumerate(tones):
        seg = x[a:b]
        f = peak_freq(seg)
        p = points[i] if i < len(points) else {"label": "?", "value": float("nan")}
        e = expected[i] if i < len(expected) else float("nan")
        before = [c for c in clicks if c[1] <= a]
        c_at = f"{before[-1][0] / RATE:7.3f} s" if before else "   none "
        rms = float(np.sqrt(np.mean(seg ** 2)))
        tone_stats.append((float(np.max(np.abs(seg))), rms))
        lines.append(f"{i + 1:2d}  {p['label']:<6} {p['value']:6.1f}  {c_at}  {a / RATE:7.3f} s  {(b - a) / RATE * 1000:5.0f} ms"
                     f"  {f:8.1f}  {e:8.1f}  {tone_stats[-1][0]:.3f}  {rms:.3f}")
        if abs(f - e) / e > 0.01:
            problems.append(f"tone {i + 1}: {f:.1f} Hz, expected {e:.1f} Hz for {p['value']}")
        tone_stats[-1] += (f,)

    lines.append("")
    if len(tones) != len(points):
        problems.append(f"{len(tones)} tones for {len(points)} data points")
    if len(clicks) != len(points):
        problems.append(f"{len(clicks)} clicks for {len(points)} categories")
    freqs = [t[2] for t in tone_stats]
    data_vals = [p["value"] for p in points]
    for name, vals in [("extracted values", data_vals)] + ([("given values", values)] if values else []):
        ok = len(vals) == len(freqs) and rank(freqs) == rank(vals)
        lines.append(f"Pitch order vs {name} {vals}: {'matches' if ok else 'DOES NOT match'}"
                     f" (highest pitch → lowest: {[points[i]['label'] for i in np.argsort(freqs)[::-1]]})")
        if not ok:
            problems.append(f"pitch order does not match the {name}")
    if values and len(values) == len(data_vals) and any(abs(a - b) > 1e-9 for a, b in zip(values, data_vals)):
        lines.append(f"Note: the export's verified values {data_vals} differ from the given values {values};"
                     " the tones follow the export's.")

    # Clicks before tones: each tone preceded by its own click 50 ms earlier, and no click inside a tone.
    for i, (a, b) in enumerate(tones):
        own = [c for c in clicks if 0 < (a - c[0]) / RATE < 0.1]
        if len(own) != 1:
            problems.append(f"tone {i + 1}: {len(own)} clicks in the 100 ms before it, expected 1")
    for i in range(len(segs) - 1):
        if segs[i][1] > segs[i + 1][0]:
            problems.append(f"sounds overlap at {segs[i + 1][0] / RATE:.3f} s")
    sched = sorted(events, key=lambda e: e["at"])
    for e1, e2 in zip(sched, sched[1:]):
        if e1["type"] == "tone" and e2["at"] < e1["at"] + 0.28:
            problems.append(f"scheduled {e2['type']} at {e2['at']:.3f} s starts inside the tone at {e1['at']:.3f} s")
    gaps = [(tones[i + 1][0] - tones[i][1]) / RATE * 1000 for i in range(len(tones) - 1)]
    if gaps:
        lines.append(f"Silence between one tone's end and the next tone's start: {min(gaps):.0f}–{max(gaps):.0f} ms (no overlap if > 0).")

    # The series name is spoken before anything sounds.
    names = [s["name"] for s in sound["series"]]
    first_sound_seq = min((e["seq"] for e in events), default=None)
    lines.append(f"Spoken: {speech}")
    if speech != names:
        problems.append(f"spoken {speech}, expected the series name(s) {names}")
    if first_sound_seq is not None and first_sound_seq < 1:
        problems.append("a sound was scheduled before the series name was spoken")
    else:
        lines.append(f"Series name \"{names[0]}\" spoken before the first click: yes")

    # Loudness and clipping.
    c_peak = max((float(np.max(np.abs(x[a:b]))) for a, b in clicks), default=0.0)
    c_rms = max((float(np.sqrt(np.mean(x[a:b] ** 2))) for a, b in clicks), default=0.0)
    t_peak = min((t[0] for t in tone_stats), default=0.0)
    t_rms = min((t[1] for t in tone_stats), default=0.0)
    lines.append(f"Loudest click: peak {c_peak:.3f}, RMS {c_rms:.3f}. Quietest tone: peak {t_peak:.3f}, RMS {t_rms:.3f} "
                 f"(click/tone peak {20 * np.log10(c_peak / t_peak):+.1f} dB, RMS {20 * np.log10(c_rms / t_rms):+.1f} dB).")
    if c_peak > t_peak or c_rms > t_rms:
        problems.append("a click is louder than the quietest tone")
    m = float(np.max(np.abs(x)))
    lines.append(f"Overall peak {m:.3f} ({20 * np.log10(m):.1f} dBFS); clipping at ≥ 1.0: {'YES' if m >= 1 else 'no'}.")
    if m >= 1:
        problems.append(f"clipping: peak {m:.3f}")
    return lines, problems


# ---------- Reading profiles ----------
def screenshots(browser, url: str, pdf_page: int, caption: str) -> list[str]:
    names = []
    for prof in PROFILES:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(f"try {{ localStorage.setItem('reading-profile', '{prof}'); }} catch (e) {{}}")
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_load_state("load")
        if prof == "keyboard":  # show the focus outline and halo on the figure's first control
            fid = figure_id(page, caption)
            page.keyboard.press("Tab")
            page.locator(f"#{fid} button").first.focus()
        box = page.evaluate("""(n) => {
            const top = document.getElementById(`page-${n}`), next = document.getElementById(`page-${n + 1}`);
            const y = (el) => el.getBoundingClientRect().top + window.scrollY;
            const end = next ? y(next) : document.documentElement.scrollHeight;
            return { y: Math.max(0, y(top) - 16), h: end - y(top) + 16, w: document.documentElement.clientWidth }; }""",
                            pdf_page)
        name = f"profile_{prof}.png"
        page.screenshot(path=str(OUT / name), full_page=True, clip={"x": 0, "y": box["y"], "width": box["w"], "height": box["h"]})
        names.append(name)
        ctx.close()
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?")
    ap.add_argument("--page", type=int, default=85)
    ap.add_argument("--figure", default="Figure 2.3")
    ap.add_argument("--values", help="comma-separated bar values to check pitch order against")
    args = ap.parse_args()
    export = Path(args.export).resolve() if args.export else newest_export()
    url = export.as_uri()
    values = [float(v) for v in args.values.split(",")] if args.values else None
    OUT.mkdir(parents=True, exist_ok=True)
    slug = args.figure.replace("Figure ", "fig").replace(" ", "")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        texts = []
        for version in ("original", "accessible"):
            name, n = blindfold(browser, url, args.page, args.figure, version)
            texts.append(name)
            print(f"{name}: {n} utterances")

        x, sound, events, speech = render_sonification(browser, url, args.figure)
        wav = OUT / f"sonify_{slug}.wav"
        write_wav(wav, x)
        lines, problems = analyse(x, sound, events, speech, values)
        report = [f"Hear the chart: {args.figure} ({sound['kind']}, \"{sound['title']}\") → {wav.name}", ""] + lines
        report += ["", f"Problems: {len(problems)}"] + [f"  - {p}" for p in problems]
        (OUT / f"sonify_{slug}.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))

        shots = screenshots(browser, url, args.page, args.figure)
        print("screenshots:", ", ".join(shots))
        browser.close()

    zpath = OUT / "listen_check.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in texts + shots:
            z.write(OUT / name, name)
    print(f"{zpath.relative_to(ROOT)}: {len(texts) + len(shots)} files")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
