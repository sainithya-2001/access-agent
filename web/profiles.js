/* Reading profiles for the exported HTML. The student picks one; it is remembered in this browser.
   Profiles change presentation only (CSS keyed on <html data-profile>); the text, alt text and
   structure are the same in every profile. Two profiles add their own controls, hidden in the
   others: Low vision's "Zoom figure" buttons, and Reading support's read-aloud bar, which reads the
   page with the browser's speech synthesis and highlights each word (CSS Custom Highlight API, so
   the page's text is never rewritten). Inlined into the export by app/assemble.py. */
(function () {
  "use strict";
  const PROFILES = ["screen-reader", "low-vision", "reading", "keyboard"];
  const KEY = "reading-profile";
  const root = document.documentElement;
  const synth = window.speechSynthesis;
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- profile switcher ----------
  const bar = document.querySelector(".profile-bar");
  if (!bar) return;
  bar.hidden = false;
  const radios = [...bar.querySelectorAll("input[name=profile]")];
  const current = () => (PROFILES.includes(root.dataset.profile) ? root.dataset.profile : "screen-reader");
  function setProfile(p) {
    if (!PROFILES.includes(p)) p = "screen-reader";
    if (p === "screen-reader") delete root.dataset.profile; else root.dataset.profile = p;
    try { localStorage.setItem(KEY, p); } catch (e) { /* private mode: this visit only */ }
    for (const r of radios) r.checked = r.value === p;
    if (p !== "reading") ra.stop();
  }
  for (const r of radios) r.addEventListener("change", () => { if (r.checked) setProfile(r.value); });

  // ---------- Low vision: zoomable figures ----------
  const LEVELS = [1, 1.5, 2, 3, 4];
  let zoom = null;
  function buildZoom() {
    const d = document.createElement("dialog");
    d.className = "zoom-dialog";
    d.setAttribute("aria-labelledby", "zoom-title");
    d.innerHTML = `
      <div class="zoom-head">
        <h2 id="zoom-title"></h2>
        <button type="button" class="zoom-out">Zoom out</button>
        <span class="zoom-level" aria-live="polite"></span>
        <button type="button" class="zoom-in">Zoom in</button>
        <button type="button" class="zoom-close">Close</button>
      </div>
      <div class="zoom-view" tabindex="0" role="group" aria-label="Zoomed figure. Scroll with the arrow keys."></div>`;
    document.body.appendChild(d);
    zoom = { d, view: d.querySelector(".zoom-view"), level: d.querySelector(".zoom-level"),
             title: d.querySelector("#zoom-title"), i: 2, opener: null };
    d.querySelector(".zoom-in").addEventListener("click", () => setLevel(zoom.i + 1));
    d.querySelector(".zoom-out").addEventListener("click", () => setLevel(zoom.i - 1));
    d.querySelector(".zoom-close").addEventListener("click", () => d.close());
    d.addEventListener("keydown", (e) => {
      if (e.key === "+" || e.key === "=") { e.preventDefault(); setLevel(zoom.i + 1); }
      else if (e.key === "-") { e.preventDefault(); setLevel(zoom.i - 1); }
    });
    d.addEventListener("close", () => { if (zoom.opener) zoom.opener.focus(); });
  }
  function setLevel(i) {
    zoom.i = Math.max(0, Math.min(LEVELS.length - 1, i));
    const img = zoom.view.querySelector("img");
    if (img) img.style.width = `${LEVELS[zoom.i] * 100}%`;
    zoom.level.textContent = `${LEVELS[zoom.i] * 100}%`;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".zoom-btn");
    if (!btn) return;
    if (!zoom) buildZoom();
    const block = document.getElementById(btn.dataset.zoom);
    const src = block && block.querySelector("figure img");
    if (!src) return;
    const img = src.cloneNode();
    img.removeAttribute("aria-describedby");
    zoom.view.replaceChildren(img);
    const cap = block.querySelector("figcaption");
    zoom.title.textContent = cap ? cap.textContent : "Figure";
    zoom.opener = btn;
    setLevel(2);
    zoom.d.showModal();
    zoom.view.focus();
  });

  // ---------- Reading support: read aloud with word highlighting ----------
  const ra = {
    items: [], i: -1, playing: false, token: 0, rate: 1,
    stop() {
      this.token++;
      this.playing = false;
      if (synth) synth.cancel();
      mark(null);
      word(null);
      this.i = -1;
      render();
    },
  };
  const raBar = bar.querySelector(".ra-bar");
  const q = (s) => raBar && raBar.querySelector(s);
  const READABLE = "h1, h2, h3, h4, h5, h6, p, li, figcaption, caption, img[alt]";
  function readable() {
    const main = document.querySelector("main");
    return [...main.querySelectorAll(READABLE)].filter((el) =>
      !el.closest(".blindfold-launch, .hear-chart, td, th") && !el.closest("li p") &&
      (el.tagName === "IMG" ? el.alt.trim() : el.textContent.trim()) &&
      !(el.tagName === "LI" && el.querySelector("p")));
  }
  function render() {
    if (!raBar) return;
    q(".ra-play").textContent = ra.playing ? "Pause" : "Play";
  }
  function mark(el) {
    for (const x of document.querySelectorAll(".ra-current")) x.classList.remove("ra-current");
    if (el) (el.tagName === "IMG" ? el.closest("figure") || el : el).classList.add("ra-current");
  }
  function word(range) {
    if (!window.CSS || !CSS.highlights) return;
    if (range) CSS.highlights.set("ra-word", new Highlight(range)); else CSS.highlights.delete("ra-word");
  }
  function rangeAt(el, start, len) {
    const text = el.textContent;
    if (!len) { const m = /\S+/.exec(text.slice(start)); len = m ? m.index + m[0].length : 0; }
    const end = start + len;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    let pos = 0, n, began = false;
    while ((n = walker.nextNode())) {
      const next = pos + n.data.length;
      if (!began && start < next) { range.setStart(n, start - pos); began = true; }
      if (began && end <= next) { range.setEnd(n, end - pos); return range; }
      pos = next;
    }
    return null;
  }
  const pieces = (text) => Sentences.chunks(text);  // whole sentences with offsets (web/sentences.js)
  function speakItem() {
    ra.token++;
    if (synth) synth.cancel();
    const tok = ra.token;
    const el = ra.items[ra.i];
    if (!el) { ra.stop(); return; }
    mark(el);
    word(null);
    (el.tagName === "IMG" ? el.closest("figure") || el : el).scrollIntoView({ block: "center", behavior: reduced() ? "auto" : "smooth" });
    const isImg = el.tagName === "IMG";
    const parts = isImg ? [[0, `Image: ${el.alt}`]] : pieces(el.textContent);
    let k = 0;
    const next = () => {
      if (tok !== ra.token || !ra.playing) return;
      if (k >= parts.length) {
        word(null);
        if (ra.i < ra.items.length - 1) { ra.i++; speakItem(); } else ra.stop();
        return;
      }
      const [off, text] = parts[k++];
      const u = new SpeechSynthesisUtterance(text);
      u.rate = ra.rate;
      u.lang = root.lang || "en";
      if (!isImg) u.onboundary = (e) => {
        if (tok !== ra.token || (e.name && e.name !== "word")) return;
        word(rangeAt(el, off + e.charIndex, e.charLength));
      };
      u.onend = next;
      u.onerror = (e) => {
        if (tok !== ra.token || e.error === "interrupted" || e.error === "canceled") return;
        ra.playing = false;
        render();
        q(".ra-status").textContent = `Your browser could not read aloud (${e.error}).`;
      };
      synth.speak(u);
    };
    setTimeout(next, 50);
  }
  function play(fromEl) {
    if (!synth) return;
    ra.items = readable();
    if (fromEl) ra.i = Math.max(0, ra.items.indexOf(fromEl));
    else if (ra.i < 0) {  // start where the reader is: the first item in view
      const i = ra.items.findIndex((el) => el.getBoundingClientRect().bottom > 0);
      ra.i = Math.max(0, i);
    }
    ra.playing = true;
    q(".ra-status").textContent = "";
    render();
    speakItem();
  }
  function pause() {
    ra.token++;
    ra.playing = false;
    if (synth) synth.cancel();
    word(null);
    render();
  }
  function step(d) {
    if (!ra.items.length) ra.items = readable();
    ra.i = Math.max(0, Math.min(ra.items.length - 1, (ra.i < 0 ? 0 : ra.i + d)));
    if (ra.playing) speakItem(); else { mark(ra.items[ra.i]); ra.items[ra.i].scrollIntoView({ block: "center" }); }
  }
  if (raBar) {
    if (!synth) raBar.hidden = true;
    q(".ra-play").addEventListener("click", () => (ra.playing ? pause() : play()));
    q(".ra-prev").addEventListener("click", () => step(-1));
    q(".ra-next").addEventListener("click", () => step(1));
    q(".ra-stop").addEventListener("click", () => ra.stop());
    q(".ra-rate").addEventListener("change", (e) => { ra.rate = Number(e.target.value); if (ra.playing) speakItem(); });
    // Reading support: clicking a paragraph reads from there (the bar's buttons do the same by keyboard).
    document.querySelector("main").addEventListener("click", (e) => {
      if (current() !== "reading" || e.target.closest("a, button, input, select, textarea")) return;
      const el = e.target.closest(READABLE);
      if (el && readable().includes(el)) play(el);
    });
  }

  setProfile(current());
})();
