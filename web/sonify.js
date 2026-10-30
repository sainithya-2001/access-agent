/* Hear the chart: plays a verified chart as tones with Web Audio. One tone per data point, left to
   right; higher values are higher pitches (220–880 Hz, two octaves); a soft click marks each
   category; each series' name is spoken (browser speech synthesis) before its tones.
   The data comes from app/sonify.py, which offers it only for bar charts, histograms, line charts
   and frequency polygons whose values passed verification. Used by the export (inlined by
   app/assemble.py) and the review screen. Buttons: .hear-chart-btn[data-sound] inside .hear-chart. */
(function () {
  "use strict";
  const AC = window.AudioContext || window.webkitAudioContext;
  const synth = window.speechSynthesis;
  const LO = 220, HI = 880, TONE = 0.26, GAP = 0.12;
  let ctx = null;
  let run = null;  // { token, nodes, btn, status, timer }
  let token = 0;

  const fmt = (v) => (Math.abs(v) >= 1000 || Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(4))));

  function pitch(v, s) {
    const lo = s.from_zero ? 0 : s.min;
    const t = s.max > lo ? (v - lo) / (s.max - lo) : 0.5;
    return LO * Math.pow(HI / LO, Math.max(0, Math.min(1, t)));
  }

  function tone(at, freq, nodes) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "triangle";
    o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(0.22, at + 0.02);
    g.gain.setValueAtTime(0.22, at + TONE - 0.06);
    g.gain.exponentialRampToValueAtTime(0.0001, at + TONE);
    o.connect(g).connect(ctx.destination);
    o.start(at);
    o.stop(at + TONE + 0.02);
    nodes.push(o);
  }

  function click(at, nodes) {  // a short filtered noise burst
    const len = Math.floor(ctx.sampleRate * 0.012);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < len; i++) ch[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = buf;
    f.type = "highpass";
    f.frequency.value = 2500;
    g.gain.value = 0.12;
    src.connect(f).connect(g).connect(ctx.destination);
    src.start(at);
    nodes.push(src);
  }

  function say(text, tok) {  // resolves when spoken (or at once without speech)
    return new Promise((resolve) => {
      if (!synth || !text) return resolve();
      synth.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = document.documentElement.lang || "en";
      let done = false;
      const end = () => { if (!done) { done = true; resolve(); } };
      u.onend = end;
      u.onerror = end;
      setTimeout(end, 400 + text.length * 120);  // some engines never fire onend
      synth.speak(u);
      if (tok !== token) end();
    });
  }

  function wait(sec, tok) {
    return new Promise((resolve) => {
      const t = setTimeout(resolve, sec * 1000);
      if (run && run.token === tok) run.timer = t;
    });
  }

  function stop(message) {
    token++;
    if (!run) return;
    for (const n of run.nodes) { try { n.stop(); } catch (e) { /* not started */ } }
    if (run.timer) clearTimeout(run.timer);
    if (synth) synth.cancel();
    run.btn.querySelector(".hear-chart-label").textContent = "Hear the chart";
    if (message !== undefined) run.status.textContent = message;
    run = null;
  }

  async function play(btn) {
    const s = JSON.parse(btn.dataset.sound);
    const status = btn.closest(".hear-chart").querySelector(".hear-chart-status");
    stop();
    if (!ctx) ctx = new AC();
    if (ctx.state === "suspended") await ctx.resume();
    const tok = ++token;
    run = { token: tok, nodes: [], btn, status, timer: null };
    btn.querySelector(".hear-chart-label").textContent = "Stop";
    const n = s.series.reduce((a, x) => a + x.points.length, 0);
    const across = s.x_label ? ` across ${s.x_label}` : "";
    status.textContent = `Playing ${s.series.length > 1 ? `${s.series.length} series, ` : ""}${n} tones${across}, `
      + `low pitch ${fmt(s.from_zero ? 0 : s.min)}, high pitch ${fmt(s.max)}.`;
    for (const series of s.series) {
      await say(series.name, tok);
      if (tok !== token) return;
      let at = ctx.currentTime + 0.08;
      for (const p of series.points) {
        click(at, run.nodes);
        tone(at + 0.05, pitch(p.value, s), run.nodes);
        at += 0.05 + TONE + GAP;
      }
      await wait(at - ctx.currentTime + 0.15, tok);
      if (tok !== token) return;
    }
    stop("Finished.");
  }

  function supported() { return Boolean(AC); }

  // Show the controls only where they work; one delegated handler serves every button.
  function reveal(root) {
    if (!supported()) return;
    for (const el of (root || document).querySelectorAll(".hear-chart[hidden]")) el.hidden = false;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".hear-chart-btn");
    if (!btn) return;
    if (run && run.btn === btn) stop("Stopped.");
    else play(btn).catch(() => stop("Your browser could not play the sound."));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && run && document.activeElement === run.btn) stop("Stopped.");
  });

  window.Sonify = { reveal, stop, supported };
  reveal();
})();
