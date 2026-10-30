/* Blindfold mode: black out the screen and read a document aloud with the browser's own speech
   synthesis, block by block, in reading order. Two versions:
   - Original: what a screen reader gets from the untreated PDF (built at export time and embedded
     as JSON in #blindfold-original): text blocks with no headings, figures only "Image", tables a
     stream of cells.
   - Accessible: read from the exported page itself: headings, alt text, long descriptions, and
     each table cell with its headers.
   Off by default; it only runs when someone opens it. The dialog is modal, the large caption is
   hidden from assistive technology (a screen reader user hears the speech, not the caption twice),
   and speech stops when it closes.
   Used by the exported HTML (inlined by app/assemble.py) and the review screen (web/app.js). */
(function () {
  "use strict";
  const synth = window.speechSynthesis;
  const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2];
  const CHUNK = 220;  // characters per utterance: some browsers cut off long utterances

  // ---------- blocks ----------
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();

  function accessibleBlocks(main) {
    const out = [];
    let page = null, region = null;  // region: the figure or table a block belongs to
    const add = (kind, text, say) => {
      text = clean(text);
      if (text) out.push({ kind, text, say: say === undefined ? text : clean(say), page, region });
    };
    const within = (id, fn) => { const prev = region; region = region || id; fn(); region = prev; };
    const markPage = (el) => {
      const m = [...el.querySelectorAll("[id^='page-']")].pop();
      if (m) page = Number(m.id.slice(5)) || page;
    };
    const table = (t) => {
      const caption = clean(t.querySelector("caption")?.textContent).replace(/\.$/, "");
      const heads = [...t.querySelectorAll("thead th")].map((th) => clean(th.textContent));
      const rows = [...t.querySelectorAll("tbody tr")];
      const ncols = Math.max(heads.length, ...rows.map((r) => r.children.length));
      add("Table", `${caption || "Table"}. ${rows.length} rows, ${ncols} columns.`,
          `Table. ${caption}. ${rows.length} rows, ${ncols} columns.`);
      rows.forEach((tr, k) => {
        const cells = [...tr.children];
        const rowHead = cells.find((c) => c.tagName === "TH");
        const parts = [];
        cells.forEach((c, i) => {
          if (c === rowHead) return;
          const v = clean(c.textContent) || "blank";
          parts.push(c.colSpan > 1 || !heads[i] ? v : `${heads[i]}: ${v}`);
        });
        const head0 = rowHead && heads[cells.indexOf(rowHead)];
        const lead = rowHead ? `${head0 ? `${head0}: ` : ""}${clean(rowHead.textContent)}. ` : "";
        add(`Table row ${k + 1} of ${rows.length}`, lead + parts.join(". "), `Row ${k + 1}. ${lead}${parts.join(". ")}`);
      });
    };
    const walk = (el) => {
      for (const n of el.children) {
        if (n.hidden || n.getAttribute("aria-hidden") === "true" || n.classList.contains("blindfold-launch")) continue;
        const tag = n.tagName.toLowerCase();
        if (tag === "script" || tag === "style") continue;
        if (/^h[1-6]$/.test(tag)) add(`Heading level ${tag[1]}`, n.textContent, `Heading level ${tag[1]}. ${n.textContent}`);
        else if (n.classList.contains("page-marker-line")) { markPage(n); add("Page", n.textContent); }
        else if (n.classList.contains("long-desc")) {
          const [label, ...paras] = n.children;
          paras.forEach((p, i) => add("Description", p.textContent,
                                      i ? p.textContent : `${clean(label.textContent)}. ${p.textContent}`));
        } else if (n.classList.contains("math")) {
          add("Math", `${n.getAttribute("aria-label") || "Equation"}: ${n.textContent}`);
        } else if (tag === "ul" || tag === "ol") {
          const items = [...n.children].filter((li) => li.tagName === "LI");
          items.forEach((li, i) => add("List item", li.textContent,
                                       i ? li.textContent : `List, ${items.length} item${items.length === 1 ? "" : "s"}. ${li.textContent}`));
        } else if (tag === "img") {
          const alt = n.getAttribute("alt");
          if (alt) add("Image", alt, `Image. ${alt}`);  // alt="" is decorative: a screen reader skips it
        } else if (tag === "figcaption") add("Caption", n.textContent);
        else if (tag === "table") within(n.id.startsWith("tbl-") ? n.id.slice(4) : null, () => table(n));
        else if (n.classList.contains("figure-block")) within(n.id.replace(/^fig-/, ""), () => walk(n));
        else if (tag === "p" || tag === "li") { markPage(n); add("Text", n.textContent); }
        else walk(n);  // section, div, figure
      }
    };
    walk(main);
    return out;
  }

  function originalBlocks(list) {
    return (list || []).map((b) => b.kind === "Image"
      ? { kind: "Image", text: "Image", say: "Image.", page: b.page, region: b.region || null }
      : { kind: "Text", text: clean(b.text), say: clean(b.text), page: b.page, region: b.region || null });
  }

  function chunks(text) {  // whole sentences (web/sentences.js), never "Table 2." + "10 shows"
    return Sentences.chunks(text, CHUNK).map(([, c]) => c.trim()).filter(Boolean);
  }

  // ---------- storage (a per-viewer convenience; works without it) ----------
  const load = (k, d) => { try { return localStorage.getItem(`blindfold-${k}`) ?? d; } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(`blindfold-${k}`, v); } catch (e) { /* private mode */ } };

  // ---------- dialog ----------
  const CSS = `
dialog.bf { position: fixed; inset: 0; width: 100vw; height: 100vh; max-width: none; max-height: none;
  margin: 0; border: 0; padding: 1rem clamp(1rem, 4vw, 3rem); background: #000; color: #fff; overflow-y: auto;
  font: 1rem/1.5 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
dialog.bf::backdrop { background: #000; }
dialog.bf[open] { display: flex; flex-direction: column; gap: 1rem; }
.bf-top { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem 1.5rem; }
.bf-title { font-size: 1.25rem; margin: 0; margin-right: auto; color: #fff; }
.bf fieldset { border: 1px solid #8c8c8c; border-radius: 6px; padding: 0.25rem 0.75rem; margin: 0;
  display: flex; gap: 1rem; align-items: center; }
.bf legend { padding: 0 0.25rem; color: #d9d9d9; font-size: 0.9rem; }
.bf label { display: inline-flex; gap: 0.4rem; align-items: center; cursor: pointer; }
.bf input[type=radio] { width: 1.1rem; height: 1.1rem; accent-color: #ffd166; }
.bf button, .bf select { font: inherit; color: #fff; background: #1c1c1c; border: 2px solid #bdbdbd;
  border-radius: 6px; padding: 0.45rem 0.9rem; min-height: 2.75rem; cursor: pointer; }
.bf button:hover, .bf select:hover { background: #2e2e2e; }
.bf :focus-visible { outline: 3px solid #ffd166; outline-offset: 2px; }
.bf-stage { flex: 1 0 auto; display: flex; flex-direction: column; justify-content: center;
  max-width: 60rem; width: 100%; margin: 0 auto; }
.bf-kind { margin: 0 0 0.75rem; color: #ffd166; font-size: clamp(1rem, 2vw, 1.35rem); font-weight: 600;
  letter-spacing: 0.02em; }
.bf-text { margin: 0; font-size: clamp(1.4rem, 3.4vw, 2.6rem); line-height: 1.35; overflow-wrap: anywhere; }
.bf-text.long { font-size: clamp(1.15rem, 2.2vw, 1.7rem); }
.bf-pos { margin: 0; color: #d9d9d9; text-align: center; }
.bf-controls { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 0.75rem; }
.bf-play { min-width: 7rem; background: #ffd166 !important; color: #000 !important; border-color: #ffd166 !important;
  font-weight: 700; }
.bf-help { margin: 0; color: #d9d9d9; font-size: 0.9rem; text-align: center; }
.bf-status { margin: 0; color: #ffb3b3; text-align: center; min-height: 1.5em; }
.bf kbd { font: inherit; border: 1px solid #8c8c8c; border-radius: 4px; padding: 0 0.3rem; }
@media (max-width: 40rem) { .bf-title { width: 100%; } }`;

  let ui = null;
  const st = { sets: {}, version: "accessible", i: 0, playing: false, rate: 1, token: 0, opener: null };

  function build() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    const d = document.createElement("dialog");
    d.className = "bf";
    d.setAttribute("aria-labelledby", "bf-title");
    d.setAttribute("aria-describedby", "bf-about");
    d.innerHTML = `
      <div class="bf-top">
        <h2 class="bf-title" id="bf-title">Blindfold mode</h2>
        <fieldset><legend>Version</legend>
          <label><input type="radio" name="bf-version" value="original"> Original PDF</label>
          <label><input type="radio" name="bf-version" value="accessible"> Accessible</label>
        </fieldset>
        <button type="button" class="bf-close">Close</button>
      </div>
      <p class="bf-help" id="bf-about">Reads the document aloud with your browser's voice, the way a screen reader
        would. If you already use a screen reader, you don't need this.</p>
      <div class="bf-stage" aria-hidden="true">
        <p class="bf-kind"></p>
        <p class="bf-text"></p>
      </div>
      <p class="bf-pos"></p>
      <div class="bf-controls">
        <button type="button" class="bf-top-btn">Start from the top</button>
        <button type="button" class="bf-prev">Previous</button>
        <button type="button" class="bf-play">Play</button>
        <button type="button" class="bf-next">Next</button>
        <label>Speed <select class="bf-rate">${RATES.map((r) => `<option value="${r}">${r}×</option>`).join("")}</select></label>
      </div>
      <p class="bf-help"><kbd>Space</kbd> play or pause · <kbd>←</kbd> <kbd>→</kbd> previous or next ·
        <kbd>+</kbd> <kbd>−</kbd> speed · <kbd>Esc</kbd> close</p>
      <p class="bf-status" role="status"></p>`;
    document.body.appendChild(d);
    const q = (s) => d.querySelector(s);
    ui = { d, kind: q(".bf-kind"), text: q(".bf-text"), pos: q(".bf-pos"), play: q(".bf-play"),
           rate: q(".bf-rate"), status: q(".bf-status") };
    q(".bf-top-btn").addEventListener("click", () => go(0));
    q(".bf-prev").addEventListener("click", () => go(st.i - 1));
    q(".bf-next").addEventListener("click", () => go(st.i + 1));
    ui.play.addEventListener("click", () => setPlaying(!st.playing));
    q(".bf-close").addEventListener("click", () => d.close());
    ui.rate.addEventListener("change", () => setRate(Number(ui.rate.value)));
    for (const r of d.querySelectorAll("input[name=bf-version]")) {
      r.addEventListener("change", () => { if (r.checked) setVersion(r.value); });
    }
    d.addEventListener("keydown", onKey);
    d.addEventListener("close", () => {
      stop();
      if (st.opener && document.contains(st.opener)) st.opener.focus();
    });
  }

  function onKey(e) {
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || t.matches("input, select")) return;  // radios and the select use arrows
    if (e.key === " " && t.tagName !== "BUTTON") { e.preventDefault(); setPlaying(!st.playing); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(st.i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(st.i - 1); }
    else if (e.key === "+" || e.key === "=") { e.preventDefault(); stepRate(1); }
    else if (e.key === "-") { e.preventDefault(); stepRate(-1); }
  }

  // ---------- playback ----------
  const blocks = () => st.sets[st.version] || [];

  function render() {
    const b = blocks()[st.i];
    ui.kind.textContent = b ? b.kind : "";
    ui.text.textContent = b ? b.text : "Nothing to read.";
    ui.text.classList.toggle("long", !!b && b.text.length > 280);
    const name = st.version === "original" ? "Original PDF" : "Accessible";
    ui.pos.textContent = b ? `${name} · block ${st.i + 1} of ${blocks().length}` + (b.page ? ` · PDF page ${b.page}` : "") : name;
    ui.play.textContent = st.playing ? "Pause" : "Play";
  }

  function stop() {
    st.token++;
    if (synth) synth.cancel();
  }

  function speak() {
    stop();
    const tok = st.token;
    const b = blocks()[st.i];
    if (!b) return;
    const parts = chunks(b.say || b.text);
    let k = 0;
    const next = () => {
      if (tok !== st.token || !st.playing) return;
      if (k >= parts.length) {
        if (st.i < blocks().length - 1) go(st.i + 1);
        else { st.playing = false; render(); }
        return;
      }
      const u = new SpeechSynthesisUtterance(parts[k++]);
      u.rate = st.rate;
      u.lang = document.documentElement.lang || "en";
      u.onend = next;
      u.onerror = (ev) => {
        if (tok !== st.token || ev.error === "interrupted" || ev.error === "canceled") return;
        st.playing = false;
        render();
        ui.status.textContent = `Your browser could not speak (${ev.error}). Use Previous and Next to step through.`;
      };
      synth.speak(u);
    };
    setTimeout(next, 50);  // some browsers drop an utterance queued in the same tick as cancel()
  }

  function go(i) {
    const n = blocks().length;
    if (!n) return;
    st.i = Math.max(0, Math.min(n - 1, i));
    render();
    if (st.playing) speak();
  }

  function setPlaying(on) {
    st.playing = on && !!synth;
    ui.status.textContent = synth ? "" : "This browser has no speech synthesis.";
    render();
    if (st.playing) speak(); else stop();
  }

  function setRate(r) {
    st.rate = r;
    ui.rate.value = String(r);
    save("rate", r);
    if (st.playing) speak();  // the new speed from the start of this block
  }

  function stepRate(d) {
    const i = RATES.indexOf(st.rate);
    setRate(RATES[Math.max(0, Math.min(RATES.length - 1, (i < 0 ? 1 : i) + d))]);
  }

  // A figure's first block, else the first block on its page (a figure with no blocks of its own).
  function placeOf(at) {
    if (!at) return 0;
    const i = at.region ? blocks().findIndex((b) => b.region === at.region) : -1;
    if (i >= 0) return i;
    return Math.max(0, at.page ? blocks().findIndex((b) => b.page >= at.page) : 0);
  }

  function setVersion(v) {
    // Keep the place: the same figure or table, else the same PDF page, in the other version.
    const b = blocks()[st.i];
    st.version = v;
    save("version", v);
    for (const r of ui.d.querySelectorAll("input[name=bf-version]")) r.checked = r.value === v;
    st.i = placeOf(b);
    render();
    if (st.playing) speak();
  }

  /* Open over the page. source: {main: the exported page's <main> (live or parsed), original: the
     #blindfold-original list}. opener: where focus goes back to on close. at: {region, page} to start
     at that figure (the review screen's selected figure); omitted, it starts at the top. */
  function open(source, opener, at) {
    if (!ui) build();
    st.sets = { accessible: accessibleBlocks(source.main), original: originalBlocks(source.original) };
    st.opener = opener || document.activeElement;
    st.rate = RATES.includes(Number(load("rate", 1))) ? Number(load("rate", 1)) : 1;
    ui.rate.value = String(st.rate);
    st.version = load("version", "accessible") === "original" ? "original" : "accessible";
    for (const r of ui.d.querySelectorAll("input[name=bf-version]")) r.checked = r.value === st.version;
    st.i = placeOf(at);
    st.playing = false;
    ui.status.textContent = "";
    render();
    ui.d.showModal();
    ui.play.focus();
    setPlaying(true);  // opened on purpose: start reading
  }

  function fromDocument(doc) {
    const data = doc.getElementById("blindfold-original");
    let original = [];
    try { original = JSON.parse(data ? data.textContent : "[]"); } catch (e) { original = []; }
    return { main: doc.querySelector("main"), original };
  }

  window.Blindfold = { open, fromDocument, accessibleBlocks, supported: !!synth };

  // The exported page: reveal its launcher (hidden without script or speech support).
  const launch = document.getElementById("blindfold-open");
  if (launch && document.getElementById("blindfold-original")) {
    if (synth) launch.closest(".blindfold-launch").hidden = false;
    launch.addEventListener("click", () => open(fromDocument(document), launch));
  }
})();
