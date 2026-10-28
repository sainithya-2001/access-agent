// Access Agent review screen: a work queue of figures, the page they sit on, and the review panel.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { docs: [], docId: null, doc: null, figs: [], selected: null, dirty: false, poll: null };

const STATUS_LABEL = {
  needs_review: "Needs review", auto_approved: "Auto-approved", approved: "Approved by staff",
  edited: "Edited by staff", rejected: "Rejected", pending: "Pending",
};
const ACTION_LABEL = {
  classify: "Classified", extract_values: "Read the figure", describe: "Described", verify: "Verified",
  rewrite: "Rewrote", recrop: "Re-cropped", reread_value: "Re-read a value", approve: "Approve request",
  escalate: "Escalated", mark_decorative: "Marked decorative", finish: "Decision", error: "Error",
};
const AGENT_ACTIONS = new Set(["rewrite", "recrop", "reread_value", "approve", "escalate", "mark_decorative"]);
const PASS_THRESHOLD = 0.8; // app/agent/tools.py
const ALT_RECOMMENDED = 150;

// ---------- helpers ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
const docPath = (id) => `/api/documents/${encodeURIComponent(id)}`;
const figPath = (id) => `/api/figures/${encodeURIComponent(id)}`;
function announce(msg) { const a = $("announcer"); a.textContent = ""; setTimeout(() => { a.textContent = msg; }, 30); }
function banner(msg, kind = "") {
  const b = $("banner");
  b.hidden = !msg; b.textContent = msg || ""; b.className = "banner" + (kind ? " " + kind : "");
}
function store(key, val) { try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (_) { return null; } }
function figName(f) {
  const m = (f.caption || "").match(/^\s*(Figure|Fig\.|Table)\s*[\d.]*\d/i);
  return m ? m[0].trim() : `${f.type === "equation" ? "Equation" : "Figure"} on page ${f.page}`;
}
function typeLabel(t) { return t ? t.replace("_", " ") : "not classified"; }
function needsReview(f) { return f.status === "needs_review" || f.status === "pending"; }
function pageInfo(n) { return (state.doc?.page_list || []).find((p) => p.number === n); }
function pageLabel(n) {
  const p = pageInfo(n);
  return p && p.printed ? `Page ${p.printed} · PDF page ${n}` : `PDF page ${n}`;
}
function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function visibleFigs() { return $("filter-review").checked ? state.figs.filter(needsReview) : state.figs; }

// ---------- documents ----------
async function loadDocs(preferred) {
  state.docs = await api("/api/documents");
  const sel = $("doc-select");
  sel.innerHTML = state.docs.length
    ? state.docs.map((d) => `<option value="${esc(d.id)}">Pages ${esc(d.pages)} · ${esc(d.name.replace(/\.pdf$/i, ""))}</option>`).join("")
    : `<option value="">No documents yet</option>`;
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("doc");
  const want = [preferred, fromHash, state.docId].find((id) => id && state.docs.some((d) => d.id === id));
  const id = want || state.docs.find((d) => d.status === "ready")?.id || state.docs[0]?.id;
  if (id) { sel.value = id; await loadDoc(id); } else { renderEmpty(); }
}

async function loadDoc(id) {
  clearTimeout(state.poll);
  if (id !== state.docId) { state.selected = null; state.dirty = false; }
  state.docId = id;
  state.doc = await api(docPath(id));
  state.figs = await api(`${docPath(id)}/figures`);
  const ready = state.doc.status === "ready";
  for (const a of [$("export-link"), $("preview-link")]) {
    a.setAttribute("aria-disabled", String(!ready));
    a.href = ready ? `${docPath(id)}/${a.id === "export-link" ? "export" : "preview"}` : "#";
  }
  if (state.doc.status === "failed") banner(`Processing failed: ${state.doc.error}`, "error");
  else if (!ready) banner(`Processing ${state.doc.name}: ${state.doc.progress || "queued"}…`);
  else banner("");
  if (!ready && state.doc.status !== "failed") state.poll = setTimeout(() => loadDoc(id), 2000);

  renderSummary();
  renderQueue();
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("fig");
  const keep = [state.selected, fromHash].find((x) => x && state.figs.some((f) => f.id === x));
  const first = keep || (state.figs.find(needsReview) || state.figs[0])?.id;
  if (first && !state.dirty) select(first, { focus: false });
  else if (!first) renderNoFigure();
}

function renderEmpty() {
  $("summary").textContent = "";
  $("queue-list").innerHTML = "";
  $("queue-count").textContent = "";
  $("detail-body").innerHTML = `<div class="placeholder"><h2 id="detail-heading">No documents yet</h2>
    <p>Upload a PDF to start. Figures the agent is unsure about will appear here.</p></div>`;
  $("actionbar").hidden = true;
}
function renderNoFigure() {
  $("detail-body").innerHTML = `<div class="placeholder"><h2 id="detail-heading">No figures found yet</h2>
    <p>${state.doc?.status === "ready" ? "This document has no figures to review." : "Figures appear here as the agent finishes them."}</p></div>`;
  $("actionbar").hidden = true;
  $("page-heading").textContent = "Page";
  $("page-image").removeAttribute("src");
  $("boxes").innerHTML = "";
}

// ---------- summary + queue ----------
function renderSummary() {
  const n = state.figs.filter(needsReview).length;
  const auto = state.figs.filter((f) => f.status === "auto_approved").length;
  const human = state.figs.filter((f) => ["approved", "edited", "rejected"].includes(f.status)).length;
  $("summary").innerHTML = `<strong class="n-review">${n}</strong> need review<span class="sep" aria-hidden="true">·</span>`
    + `<strong>${auto}</strong> auto-approved`
    + (human ? `<span class="sep" aria-hidden="true">·</span><strong>${human}</strong> reviewed by staff` : "");
}

function renderQueue() {
  const figs = visibleFigs();
  $("queue-count").textContent = `${figs.length} of ${state.figs.length}`;
  $("queue-empty").hidden = figs.length > 0 || !state.figs.length;
  $("queue-list").innerHTML = figs.map((f) => {
    const conf = f.confidence == null ? "" : f.confidence.toFixed(2);
    const label = `${figName(f)}, ${STATUS_LABEL[f.status]}, ${pageLabel(f.page)}`
      + (conf ? `, confidence ${conf}` : "");
    return `<li><button type="button" class="queue-item" data-id="${esc(f.id)}" aria-label="${esc(label)}"
      ${f.id === state.selected ? 'aria-current="true"' : ""}>
      <span class="dot ${f.status}" aria-hidden="true"></span>
      <span class="q-title" aria-hidden="true">${esc(figName(f))}</span>
      <span class="q-conf" aria-hidden="true">${conf}</span>
      <span class="q-meta" aria-hidden="true">${esc(pageLabel(f.page))} · ${esc(typeLabel(f.type))}</span>
    </button></li>`;
  }).join("");
}

function queueButton(id) { return $("queue-list").querySelector(`[data-id="${CSS.escape(id)}"]`); }

// ---------- selection ----------
function select(id, { focus = "none" } = {}) {
  if (state.dirty && id !== state.selected && !confirm("Discard your unsaved edits to this figure?")) return;
  const f = state.figs.find((x) => x.id === id);
  if (!f) return;
  state.selected = id; state.dirty = false;
  const params = new URLSearchParams({ doc: state.docId, fig: id });
  history.replaceState(null, "", "#" + params.toString());
  for (const b of $("queue-list").querySelectorAll(".queue-item")) {
    if (b.dataset.id === id) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
  }
  renderPage(f);
  renderDetail(f);
  const qb = queueButton(id);
  if (qb) qb.scrollIntoView({ block: "nearest" });
  if (focus === "queue" && qb) qb.focus();
  if (focus === "detail") $("detail-heading").focus();
}

function move(step) {
  const figs = visibleFigs();
  if (!figs.length) return;
  const i = figs.findIndex((f) => f.id === state.selected);
  const next = figs[(i + step + figs.length) % figs.length];
  select(next.id, { focus: "queue" });
  announce(`${figName(next)}, ${STATUS_LABEL[next.status]}`);
}

// ---------- page view ----------
function renderPage(f) {
  const p = pageInfo(f.page);
  $("page-heading").textContent = pageLabel(f.page);
  const img = $("page-image");
  const src = `${docPath(state.docId)}/pages/${f.page}/image`;
  if (img.getAttribute("src") !== src) { img.setAttribute("src", src); }
  img.alt = `${pageLabel(f.page)} with figure boxes`;
  const onPage = state.figs.filter((x) => x.page === f.page);
  const filtered = $("filter-review").checked;
  $("boxes").innerHTML = p ? onPage.map((x) => {
    const b = x.bbox;
    const style = `left:${(b.x0 / p.width) * 100}%;top:${(b.y0 / p.height) * 100}%;`
      + `width:${((b.x1 - b.x0) / p.width) * 100}%;height:${((b.y1 - b.y0) / p.height) * 100}%`;
    const cls = ["box", x.status, x.id === f.id ? "selected" : "", filtered && !needsReview(x) ? "dim" : ""].join(" ");
    return `<div class="${cls}" style="${style}" data-id="${esc(x.id)}" title="${esc(figName(x))}: ${esc(STATUS_LABEL[x.status])}">
      ${x.id === f.id ? `<span class="tag">${esc(figName(x))}</span>` : ""}</div>`;
  }).join("") : "";
  const sel = $("boxes").querySelector(".box.selected");
  if (sel) {
    const scroller = $("page-scroll");
    const top = sel.offsetTop - 40;
    if (top < scroller.scrollTop || top + sel.offsetHeight > scroller.scrollTop + scroller.clientHeight) {
      scroller.scrollTo({ top: Math.max(0, top), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }
}

// ---------- detail panel ----------
function confidenceHtml(f) {
  if (f.confidence == null) {
    return `<p class="fig-sub">${f.decorative ? "Decorative: no description needed, so nothing to verify." : "Not verified yet."}</p>`;
  }
  const pct = Math.round(f.confidence * 100);
  return `<div class="confidence">
    <span class="c-label">Verifier confidence</span>
    <span class="c-value">${f.confidence.toFixed(2)} <span class="hint">· ${f.passed ? "passed" : "did not pass"}</span></span>
    <div class="c-bar" aria-hidden="true"><div class="c-fill ${f.passed ? "pass" : ""}" style="width:${pct}%"></div>
      <div class="c-threshold" style="left:${PASS_THRESHOLD * 100}%"></div></div>
    <span class="c-note">Auto-approval needs ${PASS_THRESHOLD.toFixed(2)} and no failed checks (marker on the bar).</span>
  </div>`;
}

function traceClass(t) {
  if (t.action === "verify") return t.summary.startsWith("PASS") ? "pass" : "fail";
  if (AGENT_ACTIONS.has(t.action)) return "agent";
  return t.action === "finish" ? "finish" : t.action === "error" ? "error" : "";
}

function valuesHtml(f) {
  const rows = f.data.filter((d) => d.label);
  if (!rows.length) return "";
  const series = rows.some((d) => d.series);
  return `<div class="section"><details class="values"><summary>Values read from the image <span class="count">(${rows.length})</span></summary>
    <table class="values"><caption>${f.values_estimated ? "Estimated from the axis; check against the figure." : "Printed in the figure."}</caption>
    <thead><tr><th scope="col">Label</th>${series ? '<th scope="col">Series</th>' : ""}<th scope="col" class="num">Value</th></tr></thead>
    <tbody>${rows.map((d) => `<tr><th scope="row">${esc(d.label)}</th>${series ? `<td>${esc(d.series || "")}</td>` : ""}
      <td class="num">${d.value == null ? "—" : esc(d.value)}</td></tr>`).join("")}</tbody></table></details></div>`;
}

function historyHtml(f) {
  if (!f.edits.length) return "";
  const verb = { approve: "Approved", edit: "Edited and approved", reject: "Rejected" };
  return `<div class="section"><h3>Review history</h3><ul class="history">${f.edits.map((e) =>
    `<li>${verb[e.action]} by <strong>${esc(e.who)}</strong> · <time datetime="${esc(e.at)}">${esc(fmtTime(e.at))}</time>`
    + `${e.note ? `: “${esc(e.note)}”` : ""}</li>`).join("")}</ul></div>`;
}

function renderDetail(f) {
  const name = figName(f);
  const reason = f.status === "rejected"
    ? `<p class="reason rejected"><strong>Rejected.</strong> The export flags this figure until it has a new description.</p>`
    : f.reason && needsReview(f) ? `<p class="reason"><strong>Why it's here:</strong> ${esc(f.reason)}</p>` : "";
  const caption = f.caption && f.caption.trim() !== name ? `<p class="fig-sub">${esc(f.caption)}</p>` : "";
  const issues = f.issues.length
    ? `<ul class="issues">${f.issues.map((i) => `<li><span class="issue-kind">${esc(i.kind.replace(/_/g, " "))}</span>`
      + `<span class="issue-src">${i.source === "code" ? "code check" : "model check"}</span><br>${esc(i.detail)}</li>`).join("")}</ul>`
    : `<p class="no-issues">No issues found in the last check.</p>`;
  $("detail-body").innerHTML = `
    <div class="eyebrow"><span class="badge ${f.status}"><span class="dot ${f.status}" aria-hidden="true"></span>${STATUS_LABEL[f.status]}</span>
      <span>${esc(typeLabel(f.type))}</span><span aria-hidden="true">·</span><span>${esc(pageLabel(f.page))}</span></div>
    <h2 class="fig-title" id="detail-heading" tabindex="-1">${esc(name)}</h2>
    ${caption}
    ${reason}
    ${confidenceHtml(f)}
    <figure class="crop"><img src="${figPath(f.id)}/crop" alt="${esc(name)} as cropped from the page"></figure>
    <form id="desc-form" novalidate>
      <div class="field">
        <label for="alt-text">Alt text <span class="counter" id="alt-counter" aria-live="off"></span></label>
        <textarea id="alt-text" rows="3" maxlength="300" aria-describedby="alt-hint">${esc(f.alt_text)}</textarea>
        <p class="hint" id="alt-hint">${f.decorative ? "Marked decorative, so the export uses empty alt text." : `One or two sentences, up to about ${ALT_RECOMMENDED} characters.`}</p>
      </div>
      <div class="field">
        <label for="long-desc">Long description</label>
        <textarea id="long-desc" rows="7" aria-describedby="long-hint">${esc(f.long_description)}</textarea>
        <p class="hint" id="long-hint">Linked from the image in the export. Charts also get a data table from the values below.</p>
      </div>
      ${f.latex ? `<div class="field"><span class="label">Equation (LaTeX, exported as MathML)</span><pre class="math-src">${esc(f.latex)}</pre></div>` : ""}
    </form>
    <div class="section"><h3>Verifier issues <span class="count">(${f.issues.length})</span></h3>${issues}</div>
    ${valuesHtml(f)}
    <div class="section"><h3>How the agent got here</h3>
      <ol class="timeline">${f.trace.map((t) => `<li class="${traceClass(t)}"><span class="t-dot" aria-hidden="true"></span>
        <span class="t-action">${esc(ACTION_LABEL[t.action] || t.action)}</span><span class="t-text">${esc(t.summary)}</span></li>`).join("")}</ol></div>
    ${historyHtml(f)}`;
  $("actionbar").hidden = false;
  $("reject-box").hidden = true;
  $("actions").hidden = false;
  for (const id of ["alt-text", "long-desc"]) $(id).addEventListener("input", onEdit);
  updateCounter();
  updateButtons();
}

function updateCounter() {
  const n = $("alt-text").value.length;
  const c = $("alt-counter");
  c.textContent = `${n} / ${ALT_RECOMMENDED}`;
  c.classList.toggle("over", n > ALT_RECOMMENDED);
}
function onEdit() {
  const f = current();
  state.dirty = $("alt-text").value !== f.alt_text || $("long-desc").value !== f.long_description;
  updateCounter();
  updateButtons();
}
function updateButtons() {
  $("approve-btn").innerHTML = (state.dirty ? "Approve with edits" : "Approve") + " <kbd>A</kbd>";
  $("discard-btn").hidden = !state.dirty;
}
function current() { return state.figs.find((x) => x.id === state.selected); }

// ---------- review actions ----------
function reviewer() {
  const who = $("reviewer").value.trim();
  if (!who) {
    banner("Enter your name in Reviewer first, so the decision is recorded with who made it.", "error");
    $("reviewer").focus();
  }
  return who;
}

async function decide(action, extra = {}) {
  const f = current();
  const who = reviewer();
  if (!f || !who) return;
  const body = { action, who, ...extra };
  if (action === "edit") { body.alt_text = $("alt-text").value.trim(); body.long_description = $("long-desc").value.trim(); }
  try {
    const updated = await api(`${figPath(f.id)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    Object.assign(f, updated);
    state.dirty = false;
    banner("");
    renderSummary();
    const left = state.figs.filter(needsReview).length;
    const verb = { approve: "Approved", edit: "Saved edits and approved", reject: "Rejected" }[action];
    // Move on to the next figure that still needs review (after this one, wrapping around).
    const i = state.figs.findIndex((x) => x.id === f.id);
    const next = [...state.figs.slice(i + 1), ...state.figs.slice(0, i)].find(needsReview);
    renderQueue();
    if (next) {
      select(next.id, { focus: "queue" });
      announce(`${verb} ${figName(f)}. ${left} left to review. Now on ${figName(next)}.`);
    } else {
      select(f.id, { focus: "detail" });
      announce(`${verb} ${figName(f)}. Nothing left to review.`);
    }
  } catch (err) {
    banner(`Could not save: ${err.message}`, "error");
  }
}

function approve() { decide(state.dirty ? "edit" : "approve"); }
function openReject() {
  $("actions").hidden = true;
  $("reject-box").hidden = false;
  $("reject-note").value = "";
  $("reject-note").focus();
}
function closeReject() {
  $("reject-box").hidden = true;
  $("actions").hidden = false;
  $("reject-btn").focus();
}

// ---------- upload ----------
async function upload(ev) {
  ev.preventDefault();
  const form = $("upload-form");
  const err = $("upload-error");
  err.textContent = "";
  if (!$("upload-file").files.length) { err.textContent = "Choose a PDF file."; $("upload-file").focus(); return; }
  $("upload-submit").setAttribute("aria-disabled", "true");
  try {
    const res = await api("/api/documents", { method: "POST", body: new FormData(form) });
    $("upload-dialog").close();
    form.reset();
    announce("Upload received. Processing started.");
    await loadDocs(res.id);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    $("upload-submit").removeAttribute("aria-disabled");
  }
}

// ---------- keyboard ----------
function shortcutsOn() { return store("shortcuts") !== "off"; }

document.addEventListener("keydown", (ev) => {
  if (document.querySelector("dialog[open]")) return;
  const t = ev.target;
  const typing = t.matches("input, textarea, select");
  if (typing) {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey) && (t.id === "alt-text" || t.id === "long-desc")) { ev.preventDefault(); approve(); }
    else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey) && t.id === "reject-note") { ev.preventDefault(); $("reject-confirm").click(); }
    else if (ev.key === "Escape" && t.id === "reject-note") { ev.preventDefault(); closeReject(); }
    else if (ev.key === "Escape" && t.tagName === "TEXTAREA") { ev.preventDefault(); $("detail-heading").focus(); }
    return;
  }
  if (ev.ctrlKey || ev.metaKey || ev.altKey || !shortcutsOn() || !state.selected) {
    if (ev.key === "?" && !ev.ctrlKey) $("shortcuts-dialog").showModal();
    return;
  }
  const k = ev.key.toLowerCase();
  if (k === "j") { ev.preventDefault(); move(1); }
  else if (k === "k") { ev.preventDefault(); move(-1); }
  else if (k === "a") { ev.preventDefault(); approve(); }
  else if (k === "r") { ev.preventDefault(); openReject(); }
  else if (k === "e") { ev.preventDefault(); $("alt-text").focus(); }
  else if (ev.key === "?") { ev.preventDefault(); $("shortcuts-dialog").showModal(); }
});

// ---------- wiring ----------
$("doc-select").addEventListener("change", (e) => { if (e.target.value) { state.dirty = false; loadDoc(e.target.value); } });
$("filter-review").addEventListener("change", (e) => {
  store("filter", e.target.checked ? "review" : "all");
  renderQueue();
  const f = current();
  if (f) renderPage(f);
  const vis = visibleFigs();
  if (vis.length && !vis.some((x) => x.id === state.selected)) select(vis[0].id);
});
$("queue-list").addEventListener("click", (e) => {
  const b = e.target.closest(".queue-item");
  if (b) select(b.dataset.id, { focus: "queue" });
});
$("queue-list").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); move(e.key === "ArrowDown" ? 1 : -1); }
});
$("boxes").addEventListener("click", (e) => {
  const b = e.target.closest(".box");
  if (b) select(b.dataset.id, { focus: "queue" });
});
$("reviewer").value = store("reviewer") || "";
$("reviewer").addEventListener("change", (e) => { store("reviewer", e.target.value.trim()); banner(""); });
$("approve-btn").addEventListener("click", approve);
$("reject-btn").addEventListener("click", openReject);
$("reject-cancel").addEventListener("click", closeReject);
$("reject-confirm").addEventListener("click", () => decide("reject", { note: $("reject-note").value.trim() || null }));
$("discard-btn").addEventListener("click", () => { const f = current(); state.dirty = false; renderDetail(f); $("alt-text").focus(); });
$("upload-open").addEventListener("click", () => $("upload-dialog").showModal());
$("upload-cancel").addEventListener("click", () => $("upload-dialog").close());
$("upload-form").addEventListener("submit", upload);
$("shortcuts-open").addEventListener("click", () => $("shortcuts-dialog").showModal());
$("shortcuts-close").addEventListener("click", () => $("shortcuts-dialog").close());
$("shortcuts-enabled").checked = shortcutsOn();
$("shortcuts-enabled").addEventListener("change", (e) => store("shortcuts", e.target.checked ? "on" : "off"));
for (const a of [$("export-link"), $("preview-link")]) {
  a.addEventListener("click", (e) => { if (a.getAttribute("aria-disabled") === "true") e.preventDefault(); });
}
window.addEventListener("beforeunload", (e) => { if (state.dirty) e.preventDefault(); });
$("filter-review").checked = store("filter") === "review";

loadDocs().catch((e) => banner(`Could not load documents: ${e.message}`, "error"));
