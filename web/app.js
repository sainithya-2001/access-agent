// Access Agent review screen: a work queue of figures, the page they sit on, and the review panel.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { docs: [], docId: null, doc: null, figs: [], selected: null, dirty: false, poll: null };

const STATUS_LABEL = {
  needs_review: "Needs review", auto_approved: "Auto-approved", approved: "Approved by staff",
  edited: "Edited by staff", rejected: "Rejected", pending: "Re-running",
};
// Why a figure is in the queue (api.reason_code), in plain language, with the next step and the
// field (or tool) that step needs. The technical reason stays under "Technical details".
// `why` can be a function of the figure, to name the actual chart parts when the agent read them.
const REASONS = {
  color_only: {
    why: (f) => {
      const p = f.parts || {}, s = p.series || [], marks = p.marks || "lines or bars";
      const named = s.length >= 2
        ? `${s.length === 2 ? "two" : s.length} ${marks} (${listWords(s.map((x) => `the ${x} ${marks === "lines or bars" ? "one" : marks.slice(0, -1)}`))})`
        : marks;
      const cue = colorFreeCue(f, marks);
      return `The chart's ${named} are told apart only by color, and nothing in the figure says which is which.`
        + (cue ? ` Without color, from the agent's reading of the values: ${cue}.` : "");
    },
    todo: "Find out which is which from the text or tables near the figure, and name each one in the long description.",
    go: "long-desc", button: "Edit the long description",
  },
  no_scale: {
    why: (f) => {
      const p = f.parts || {}, ax = p.value_axis && p.axes ? p.axes[p.value_axis] : null;
      if (!ax) return "The chart's value axis has no numbers, so exact values can't be read from it.";
      const name = ax.label ? ` (“${ax.label}”)` : "";
      return `The chart's ${p.value_axis === "x" ? "horizontal" : "vertical"} axis${name} has no numbers, so exact values can't be read from it.`;
    },
    todo: "Make sure the description compares the values (tallest, about twice as tall) rather than guessing numbers, unless the text states them.",
    go: "long-desc", button: "Edit the long description",
  },
  unconfirmed_box: {
    why: "The agent found this figure on its own but couldn't confirm that the box around it is right.",
    todo: "Compare the crop with the page. If it cuts off part of the figure or takes in extra text, adjust the crop and the agent will describe it again.",
    go: "crop", button: "Adjust crop",
  },
  checks_still_failing: {
    why: "The agent rewrote the description, but the automatic checks still found problems.",
    todo: "Read what the checks found, below, and correct the description.",
    go: "alt-text", button: "Edit the alt text",
  },
  error: {
    why: "Something went wrong while the agent worked on this figure.",
    todo: "Write the description yourself, or adjust the crop to run the agent again.",
    go: "alt-text", button: "Write the alt text",
  },
  no_description: {
    why: "There is no description yet.",
    todo: "Write the alt text, and a long description for charts and diagrams.",
    go: "alt-text", button: "Write the alt text",
  },
  agent_unsure: {
    why: "The agent wasn't sure its description is right.",
    todo: "Check the description against the figure and fix anything that's wrong.",
    go: "alt-text", button: "Edit the alt text",
  },
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
// For two series told apart only by color: how they differ in shape, so a reader who can't see
// color can still match the text's names to them ("the light blue line peaks higher"). Uses the
// agent's extracted values; a difference under 4% of the value range is too small to rely on.
function colorFreeCue(f, marks) {
  const rows = (f.data || []).filter((d) => d.series && d.value != null);
  const names = [...new Set(rows.map((d) => d.series))];
  if (names.length !== 2) return "";
  const labels = [...new Set(rows.map((d) => d.label))];
  if (labels.every((l) => !isNaN(parseFloat(l)))) labels.sort((a, b) => parseFloat(a) - parseFloat(b));
  const at = (name, label) => rows.find((d) => d.series === name && d.label === label)?.value;
  const both = labels.filter((l) => at(names[0], l) != null && at(names[1], l) != null);
  if (both.length < 2) return "";
  const all = rows.map((d) => d.value);
  const tol = 0.04 * (Math.max(...all) - Math.min(...all));
  const num = (v) => `about ${Math.round(v * 10) / 10}`;
  const one = marks === "lines" ? "line" : marks === "bars" ? "bars" : "series";
  const who = (n) => `the ${n} ${one}`;
  const cues = [];
  const peak = (n) => both.reduce((b, l) => (at(n, l) > at(n, b) ? l : b), both[0]);
  const [p0, p1] = [peak(names[0]), peak(names[1])];
  let high = null;
  if (Math.abs(at(names[0], p0) - at(names[1], p1)) > tol) {
    high = at(names[0], p0) > at(names[1], p1) ? 0 : 1;
    const [ph, pl] = high === 0 ? [p0, p1] : [p1, p0];
    cues.push(`${who(names[high])} ${marks === "lines" ? "peaks" : "reaches"} higher, ${num(at(names[high], ph))} at ${ph}, `
      + `against ${num(at(names[1 - high], pl))}`);
  }
  // Where the other one is clearly higher, or else which starts or ends higher.
  const other = high === null ? null : 1 - high;
  const diffs = both.map((l) => [l, at(names[other ?? 0], l) - at(names[1 - (other ?? 0)], l)]);
  const best = other === null ? null : diffs.reduce((b, d) => (d[1] > b[1] ? d : b));
  if (best && best[1] > tol) {
    cues.push(`${who(names[other])} is higher at ${best[0]}, ${num(at(names[other], best[0]))} against ${num(at(names[high], best[0]))}`);
  } else {
    for (const [l, word] of [[both[both.length - 1], "ends"], [both[0], "starts"]]) {
      const d = at(names[0], l) - at(names[1], l);
      if (Math.abs(d) > tol) {
        const h = d > 0 ? 0 : 1;
        cues.push(`${who(names[h])} ${word} higher, ${num(at(names[h], l))} at ${l} against ${num(at(names[1 - h], l))}`);
        break;
      }
    }
  }
  return cues.join("; ");
}
function listWords(xs) { return xs.length <= 2 ? xs.join(" and ") : `${xs.slice(0, -1).join(", ")}, and ${xs[xs.length - 1]}`; }
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
// Flagged figures first (in page order), then the rest.
function queueOrder() { return [...state.figs.filter(needsReview), ...state.figs.filter((f) => !needsReview(f))]; }
function visibleFigs() { return $("filter-review").checked ? state.figs.filter(needsReview) : queueOrder(); }

// ---------- documents ----------
// The select is narrow, so the full name shows on hover and on focus (in a tooltip under it and as
// each option's title); Esc hides the tooltip.
function docFullName(d) { return `${d.name} · pages ${d.pages}`; }
function updateDocFull() {
  const d = state.docs.find((x) => x.id === $("doc-select").value);
  $("doc-full").textContent = d ? docFullName(d) : "";
  $("doc-select").title = d ? docFullName(d) : "";
  $("doc-picker").classList.remove("tip-dismissed");
}

async function loadDocs(preferred) {
  state.docs = await api("/api/documents");
  const sel = $("doc-select");
  sel.innerHTML = state.docs.length
    ? state.docs.map((d) => `<option value="${esc(d.id)}" title="${esc(docFullName(d))}">Pages ${esc(d.pages)} · ${esc(d.name.replace(/\.pdf$/i, ""))}</option>`).join("")
    : `<option value="">No documents yet</option>`;
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("doc");
  const want = [preferred, fromHash, state.docId].find((id) => id && state.docs.some((d) => d.id === id));
  const id = want || state.docs.find((d) => d.status === "ready")?.id || state.docs[0]?.id;
  if (id) sel.value = id;
  updateDocFull();
  if (id) await loadDoc(id); else renderEmpty();
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
  $("blindfold-btn").setAttribute("aria-disabled", String(!ready));
  if (state.doc.status === "failed") banner(`Processing failed: ${state.doc.error}`, "error");
  else if (!ready) banner(`Processing ${state.doc.name}: ${state.doc.progress || "queued"}…`);
  else banner("");
  if (!ready && state.doc.status !== "failed") state.poll = setTimeout(() => loadDoc(id), 2000);

  renderSummary();
  renderQueue();
  pollPending();
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
    + `<strong>${auto}</strong> auto<span class="sum-long">-approved</span>`
    + (human ? `<span class="sep" aria-hidden="true">·</span><strong>${human}</strong> <span class="sum-long">reviewed </span>by staff` : "");
}

function renderQueue() {
  const figs = visibleFigs();
  $("queue-count").textContent = `Showing ${figs.length} of ${state.figs.length}`;
  $("queue-empty").hidden = figs.length > 0 || !state.figs.length;
  $("show-all").textContent = `Show all ${state.figs.length} figure${state.figs.length === 1 ? "" : "s"}`;
  $("queue-list").innerHTML = figs.map((f) => {
    const label = `${figName(f)}, ${STATUS_LABEL[f.status]}, ${pageLabel(f.page)}`;
    return `<li><button type="button" class="queue-item" data-id="${esc(f.id)}" aria-label="${esc(label)}"
      ${f.id === state.selected ? 'aria-current="true"' : ""}>
      <span class="dot ${f.status}" aria-hidden="true"></span>
      <span class="q-title" aria-hidden="true">${esc(figName(f))}</span>
      <span class="q-status ${f.status}" aria-hidden="true">${esc(STATUS_LABEL[f.status])}</span>
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
      ${x.id === f.id ? `<span class="tag">${esc(figName(x))} · ${esc(STATUS_LABEL[x.status])}</span>` : ""}</div>`;
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
// Secondary to the status line: the checker's score, labelled, with what it is measured against.
function scoreHtml(f) {
  if (f.confidence == null) return f.decorative ? `<p class="score">Decorative: no description needed, so nothing to check.</p>` : "";
  return `<p class="score">Checker score ${f.confidence.toFixed(2)} out of 1 · auto-approval needs at least `
    + `${PASS_THRESHOLD.toFixed(2)} and no failed checks · ${f.passed ? "these checks passed" : "these checks did not pass"}</p>`;
}

function cropSrc(f) { return `${figPath(f.id)}/crop?v=${encodeURIComponent(f.crop_version || "")}`; }
function enlargeCrop() {
  const f = current();
  if (!f) return;
  $("crop-dialog-title").textContent = `${figName(f)}: the crop the agent described`;
  const img = $("crop-dialog-image");
  img.src = cropSrc(f);
  img.alt = `${figName(f)} as cropped from the page`;
  $("crop-dialog").showModal();
}

function lastEdit(f, actions) { return [...f.edits].reverse().find((e) => actions.includes(e.action)); }

// One status line: what state the figure is in and why, in plain words, and what to do next.
function statusHtml(f) {
  const r = REASONS[f.reason_code];
  let main, todo = "", go = "", extra = "";
  if (f.status === "pending") {
    main = "The agent is describing this figure again from the new crop. This takes about a minute.";
  } else if (needsReview(f) && r) {
    main = esc(typeof r.why === "function" ? r.why(f) : r.why);
    todo = r.todo;
    go = r.go === "crop"
      ? `<button type="button" class="linklike status-go" data-action="crop">${esc(r.button)}</button>`
      : `<button type="button" class="linklike status-go" data-goto="${r.go}">${esc(r.button)}</button>`;
    const found = f.issues.filter((i) => i.detail);
    if (["checks_still_failing", "agent_unsure"].includes(f.reason_code) && found.length) {
      extra = `<ul class="found">${found.map((i) => `<li>${esc(i.detail)}</li>`).join("")}</ul>`;
    }
  } else if (f.status === "rejected") {
    const e = lastEdit(f, ["reject"]);
    main = `${e ? `By ${esc(e.who)}` : ""}${e && e.note ? `: “${esc(e.note)}”` : ""}${e ? "." : ""} The export flags it until it has a new description.`;
    todo = "Write a new description and approve it, or adjust the crop to run the agent again.";
    go = `<button type="button" class="linklike status-go" data-goto="alt-text">Edit the alt text</button>`;
  } else if (f.status === "approved" || f.status === "edited") {
    const e = lastEdit(f, ["approve", "edit"]);
    main = e ? `By ${esc(e.who)}, ${esc(fmtTime(e.at))}.` : "";
  } else if (f.status === "auto_approved") {
    main = f.decorative ? "Marked decorative, so the export gives it empty alt text."
      : "The description passed every automatic check.";
  } else {
    main = "Not checked yet.";
  }
  const draft = needsReview(f) && f.edits.length && f.edits[f.edits.length - 1].action === "save"
    ? `<p class="status-draft">Draft saved by ${esc(f.edits[f.edits.length - 1].who)}; not approved yet.</p>` : "";
  return `<div class="status-line ${f.status}">
    <p class="status-main"><span class="dot ${f.status}" aria-hidden="true"></span><strong>${STATUS_LABEL[f.status]}.</strong> ${main}</p>
    ${extra}
    ${todo ? `<p class="status-todo"><strong>What to do:</strong> ${esc(todo)} ${go}</p>` : ""}
    ${draft}
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
  const verb = { approve: "Approved", edit: "Edited and approved", reject: "Rejected", save: "Saved a draft",
                 recrop: "Drew a new crop and re-ran the agent" };
  return `<div class="section"><h3>Review history</h3><ul class="history">${f.edits.map((e) =>
    `<li>${verb[e.action]} by <strong>${esc(e.who)}</strong> · <time datetime="${esc(e.at)}">${esc(fmtTime(e.at))}</time>`
    + `${e.note ? `: “${esc(e.note)}”` : ""}</li>`).join("")}</ul></div>`;
}

// "Hear the chart" (sonify.js): the API sends sound data only for verified, approved bar charts,
// histograms, line charts and frequency polygons.
function hearHtml(f, name) {
  if (!f.sound) return "";
  return `<div class="hear-chart" hidden><button type="button" class="btn btn-quiet hear-chart-btn" data-sound="${esc(JSON.stringify(f.sound))}">`
    + `<span class="hear-chart-label">Hear the chart</span><span class="visually-hidden">: ${esc(name)}</span></button>`
    + `<span class="hint hear-chart-status" role="status"></span></div>`;
}

function renderDetail(f) {
  if (window.Sonify) Sonify.stop();
  const name = figName(f);
  const caption = f.caption && f.caption.trim() !== name ? `<p class="fig-sub">${esc(f.caption)}</p>` : "";
  const issues = f.issues.length
    ? `<ul class="issues">${f.issues.map((i) => `<li><span class="issue-kind">${esc(i.kind.replace(/_/g, " "))}</span>`
      + `<span class="issue-src">${i.source === "code" ? "code check" : "model check"}</span><br>${esc(i.detail)}</li>`).join("")}</ul>`
    : `<p class="no-issues">No issues found in the last check.</p>`;
  const pending = f.status === "pending";
  $("detail-body").innerHTML = `
    <div class="eyebrow"><span>${esc(typeLabel(f.type))}</span><span aria-hidden="true">·</span><span>${esc(pageLabel(f.page))}</span></div>
    <h2 class="fig-title" id="detail-heading" tabindex="-1">${esc(name)}</h2>
    ${caption}
    ${statusHtml(f)}
    <figure class="crop">
      <div class="crop-thumb" data-action="enlarge" title="Enlarge"><img src="${cropSrc(f)}" alt=""></div>
      <figcaption>
        <span class="crop-title">The crop the agent described</span>
        <span class="crop-links"><button type="button" class="linklike" data-action="enlarge">Enlarge<span class="visually-hidden"> the crop of ${esc(name)}</span></button>
        <button type="button" class="linklike" data-action="crop" ${pending ? "disabled" : ""}>Adjust crop</button></span>
        <span class="hint">Adjust it if it cuts off part of the figure or takes in extra text.</span>
      </figcaption></figure>
    ${hearHtml(f, name)}
    <form id="desc-form" novalidate>
      <div class="field">
        <div class="field-head"><label for="alt-text">Alt text</label><p class="counter" id="alt-counter"></p></div>
        <textarea id="alt-text" rows="3" maxlength="300" aria-describedby="alt-hint alt-counter" ${pending ? "readonly" : ""}>${esc(f.alt_text)}</textarea>
        <p class="hint" id="alt-hint">${f.decorative ? "Marked decorative, so the export uses empty alt text." : "One or two sentences. Detail goes in the long description."}</p>
      </div>
      <div class="field">
        <label for="long-desc">Long description</label>
        <textarea id="long-desc" rows="5" aria-describedby="long-hint" ${pending ? "readonly" : ""}>${esc(f.long_description)}</textarea>
        <p class="hint" id="long-hint">Linked from the image in the export. Charts also get a data table from the values below.</p>
      </div>
      ${f.latex ? `<div class="field"><span class="label">Equation (LaTeX, exported as MathML)</span><pre class="math-src">${esc(f.latex)}</pre></div>` : ""}
    </form>
    ${historyHtml(f)}
    <details class="tech" id="tech"${state.techOpen ? " open" : ""}><summary>Technical details</summary>
      ${scoreHtml(f)}
      ${f.reason ? `<p class="tech-reason"><strong>Agent's reason:</strong> ${esc(f.reason)}</p>` : ""}
      <div class="section"><h3>Checker issues <span class="count">(${f.issues.length})</span></h3>${issues}</div>
      ${valuesHtml(f)}
      <div class="section"><h3>How the agent got here</h3>
        <ol class="timeline">${f.trace.map((t) => `<li class="${traceClass(t)}"><span class="t-dot" aria-hidden="true"></span>
          <span class="t-action">${esc(ACTION_LABEL[t.action] || t.action)}</span><span class="t-text">${esc(t.summary)}</span></li>`).join("")}</ol></div>
    </details>`;
  $("tech").addEventListener("toggle", (e) => { state.techOpen = e.target.open; });
  if (window.Sonify) Sonify.reveal($("detail-body"));
  $("actionbar").hidden = false;
  $("reject-box").hidden = true;
  $("crop-panel").hidden = true;
  $("actions").hidden = false;
  for (const b of $("actions").querySelectorAll("button")) b.disabled = pending;
  for (const id of ["alt-text", "long-desc"]) $(id).addEventListener("input", onEdit);
  updateCounter();
  updateButtons();
}

function updateCounter() {
  const n = $("alt-text").value.length;
  const c = $("alt-counter");
  const over = n > ALT_RECOMMENDED;
  c.textContent = over ? `${n} characters: over the usual ${ALT_RECOMMENDED} (allowed up to 300). Consider moving detail to the long description.`
    : `${n} of about ${ALT_RECOMMENDED} characters`;
  c.classList.toggle("long", over);
}
function onEdit() {
  const f = current();
  state.dirty = $("alt-text").value !== f.alt_text || $("long-desc").value !== f.long_description;
  updateCounter();
  updateButtons();
}
function updateButtons() {
  $("discard-btn").hidden = !state.dirty;
  $("save-btn").disabled = !state.dirty;
}
function current() { return state.figs.find((x) => x.id === state.selected); }

// ---------- review actions ----------
// The reviewer's name: entered once at the top, kept in this browser.
function reviewer() {
  const who = (store("reviewer") || "").trim();
  if (!who) {
    banner("Enter your name at the top first (once), so each decision is recorded with who made it.", "error");
    showReviewerForm();
  }
  return who;
}
function showReviewer() {
  const who = (store("reviewer") || "").trim();
  $("reviewer-name").textContent = who;
  $("reviewer-name").title = `Reviewing as ${who}`;
  $("reviewer-show").hidden = !who;
  $("reviewer-form").hidden = !!who;
}
function showReviewerForm() {
  $("reviewer").value = store("reviewer") || "";
  $("reviewer-show").hidden = true;
  $("reviewer-form").hidden = false;
  $("reviewer").focus();
}

async function decide(action, extra = {}) {
  const f = current();
  const who = reviewer();
  if (!f || !who) return;
  const body = { action, who, ...extra };
  if (action === "edit" || action === "save") { body.alt_text = $("alt-text").value.trim(); body.long_description = $("long-desc").value.trim(); }
  try {
    const updated = await api(`${figPath(f.id)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    Object.assign(f, updated);
    state.dirty = false;
    banner("");
    renderSummary();
    if (action === "save") {  // a draft: stay on this figure
      renderQueue();
      select(f.id, { focus: "none" });
      $("approve-btn").focus();
      announce(`Saved your edits to ${figName(f)}. Not approved yet.`);
      return;
    }
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

// ---------- adjust crop ----------
// Draw a new box on the page image (or move/resize it with the keyboard), then re-run the agent.
const crop = { on: false, box: null, drag: null, zoom: 1, fit: 0 };
const ZOOMS = [1, 1.5, 2, 3, 4];

// Zoom the page view (while adjusting a crop): widen the page image; boxes are in % so they follow.
function setZoom(z) {
  const frame = $("page-frame");
  if (!crop.fit) crop.fit = frame.offsetWidth;
  crop.zoom = z;
  frame.style.maxWidth = z === 1 ? "" : "none";
  frame.style.width = z === 1 ? "" : `${Math.round(crop.fit * z)}px`;
  $("zoom-level").textContent = `${Math.round(z * 100)}%`;
  $("zoom-out").disabled = z === ZOOMS[0];
  $("zoom-in").disabled = z === ZOOMS[ZOOMS.length - 1];
  if (crop.on) { drawCropRect(); $("crop-rect").scrollIntoView({ block: "center", inline: "center" }); }
}
function stepZoom(dir) {
  const i = ZOOMS.indexOf(crop.zoom) + dir;
  if (i < 0 || i >= ZOOMS.length) return;
  setZoom(ZOOMS[i]);
  announce(`Zoom ${Math.round(crop.zoom * 100)}%`);
}

function pagePts() { const p = pageInfo(current().page); return { w: p.width, h: p.height }; }
function drawCropRect() {
  const { w, h } = pagePts();
  const b = crop.box;
  const r = $("crop-rect");
  r.style.left = `${(b.x0 / w) * 100}%`; r.style.top = `${(b.y0 / h) * 100}%`;
  r.style.width = `${((b.x1 - b.x0) / w) * 100}%`; r.style.height = `${((b.y1 - b.y0) / h) * 100}%`;
  r.textContent = `New crop box: ${Math.round(b.x1 - b.x0)} by ${Math.round(b.y1 - b.y0)} points`;
}
function startCrop() {
  const f = current();
  if (!f || f.status === "pending" || !pageInfo(f.page)) return;
  crop.on = true;
  crop.box = { ...f.bbox };
  $("page-frame").classList.add("cropping");
  $("zoom-controls").hidden = false;
  $("legend").hidden = true;  // the boxes are faded while cropping; the zoom controls take its place
  crop.fit = 0;
  setZoom(1);
  $("crop-rect").hidden = false;
  drawCropRect();
  $("actions").hidden = true;
  $("reject-box").hidden = true;
  $("crop-panel").hidden = false;
  $("crop-rect").focus();
  announce("Adjusting the crop. Drag on the page, or use the arrow keys on the crop box.");
}
function endCrop() {
  crop.on = false; crop.drag = null;
  $("page-frame").classList.remove("cropping");
  setZoom(1);
  $("zoom-controls").hidden = true;
  $("legend").hidden = false;
  $("crop-rect").hidden = true;
  $("crop-panel").hidden = true;
  $("actions").hidden = false;
}
function toPts(ev) {
  const r = $("page-frame").getBoundingClientRect();
  const { w, h } = pagePts();
  return { x: Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1) * w,
           y: Math.min(Math.max((ev.clientY - r.top) / r.height, 0), 1) * h };
}
$("page-frame").addEventListener("pointerdown", (ev) => {
  if (!crop.on) return;
  ev.preventDefault();
  crop.drag = toPts(ev);
  $("page-frame").setPointerCapture(ev.pointerId);
});
$("page-frame").addEventListener("pointermove", (ev) => {
  if (!crop.on || !crop.drag) return;
  const a = crop.drag, b = toPts(ev);
  crop.box = { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) };
  drawCropRect();
});
$("page-frame").addEventListener("pointerup", () => { if (crop.drag) { crop.drag = null; $("crop-rect").focus(); } });
$("crop-rect").addEventListener("keydown", (ev) => {
  const step = 4;  // points
  const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key];
  if (ev.key === "Escape") { ev.preventDefault(); endCrop(); $("detail-heading").focus(); return; }
  if (ev.key === "+" || ev.key === "=") { ev.preventDefault(); stepZoom(1); return; }
  if (ev.key === "-") { ev.preventDefault(); stepZoom(-1); return; }
  if (!d) return;
  ev.preventDefault();
  const b = crop.box;
  if (ev.shiftKey) { b.x1 = Math.max(b.x0 + 10, b.x1 + d[0]); b.y1 = Math.max(b.y0 + 10, b.y1 + d[1]); }
  else { b.x0 += d[0]; b.x1 += d[0]; b.y0 += d[1]; b.y1 += d[1]; }
  drawCropRect();
});
async function confirmCrop() {
  const f = current();
  const who = reviewer();
  if (!f || !who) return;
  try {
    const updated = await api(`${figPath(f.id)}/recrop`, {
      method: "POST", headers: { "Content-Type": "application/json", ...passcodeHeader(state.passcode) },
      body: JSON.stringify({ who, ...crop.box }),
    });
    Object.assign(f, updated);
    endCrop();
    renderSummary(); renderQueue(); select(f.id, { focus: "detail" });
    announce(`New crop saved. The agent is describing ${figName(f)} again.`);
    pollPending();
  } catch (err) {
    banner(`Could not re-run the agent: ${err.message}`, "error");
  }
}
function pollPending() {
  clearTimeout(state.pendingPoll);
  if (!state.figs.some((f) => f.status === "pending")) return;
  state.pendingPoll = setTimeout(async () => {
    const was = state.figs.filter((f) => f.status === "pending").map((f) => f.id);
    state.figs = await api(`${docPath(state.docId)}/figures`);
    renderSummary(); renderQueue();
    const done = was.filter((id) => state.figs.find((f) => f.id === id)?.status !== "pending");
    if (done.includes(state.selected) && !state.dirty) {
      select(state.selected);
      announce(`${figName(current())} is ready: ${STATUS_LABEL[current().status]}.`);
    }
    pollPending();
  }, 3000);
}

// ---------- upload ----------
// Demo mode (app/api.py DEMO_MODE): uploads and recrops run the agent, so they send the passcode.
const passcodeHeader = (p) => (state.demo && p ? { "X-Demo-Passcode": p } : {});
async function loadConfig() {
  const c = await api("/api/config");
  state.demo = c.demo_mode;
  $("upload-open").hidden = !c.uploads;
  $("demo-note").hidden = $("demo-passcode-field").hidden = !c.demo_mode;
  if (c.max_pages) {
    $("demo-max-pages").textContent = c.max_pages;
    $("upload-pages-hint").textContent = `PDF page numbers, at most ${c.max_pages} pages. Leave empty if the file is that short.`;
  }
}
async function upload(ev) {
  ev.preventDefault();
  const form = $("upload-form");
  const err = $("upload-error");
  err.textContent = "";
  if (!$("upload-file").files.length) { err.textContent = "Choose a PDF file."; $("upload-file").focus(); return; }
  if (state.demo && !$("upload-passcode").value) { err.textContent = "Enter the demo passcode."; $("upload-passcode").focus(); return; }
  $("upload-submit").setAttribute("aria-disabled", "true");
  try {
    const res = await api("/api/documents", { method: "POST", body: new FormData(form), headers: passcodeHeader($("upload-passcode").value) });
    state.passcode = $("upload-passcode").value;  // a recrop also runs the agent: reuse it this session
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
  if (crop.on) return;  // the crop box handles its own keys
  if (ev.key.toLowerCase() === "s" && (ev.ctrlKey || ev.metaKey) && state.dirty) { ev.preventDefault(); decide("save"); return; }
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
$("doc-select").addEventListener("change", (e) => { updateDocFull(); if (e.target.value) { state.dirty = false; loadDoc(e.target.value); } });
$("doc-select").addEventListener("keydown", (e) => { if (e.key === "Escape") $("doc-picker").classList.add("tip-dismissed"); });
$("doc-picker").addEventListener("mouseleave", () => $("doc-picker").classList.remove("tip-dismissed"));
$("doc-select").addEventListener("blur", () => $("doc-picker").classList.remove("tip-dismissed"));
$("show-all").addEventListener("click", () => {
  const box = $("filter-review");
  box.checked = false;
  box.dispatchEvent(new Event("change"));
  const first = $("queue-list").querySelector(".queue-item");
  if (first) first.focus();
  announce(`Showing all ${state.figs.length} figures.`);
});
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
$("reviewer-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const who = $("reviewer").value.trim();
  if (!who) { $("reviewer").focus(); return; }
  store("reviewer", who);
  banner("");
  showReviewer();
  $("reviewer-change").focus();
  announce(`Reviewing as ${who}.`);
});
$("reviewer-change").addEventListener("click", showReviewerForm);
$("save-btn").addEventListener("click", () => decide("save"));
$("crop-cancel").addEventListener("click", () => { endCrop(); $("detail-heading").focus(); });
$("crop-confirm").addEventListener("click", confirmCrop);
$("zoom-in").addEventListener("click", () => stepZoom(1));
$("zoom-out").addEventListener("click", () => stepZoom(-1));
$("crop-dialog-close").addEventListener("click", () => $("crop-dialog").close());
$("crop-dialog").addEventListener("click", (e) => { if (e.target === $("crop-dialog")) $("crop-dialog").close(); });  // backdrop
$("detail-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-goto], [data-action]");
  if (!b) return;
  if (b.dataset.action === "crop") startCrop();
  else if (b.dataset.action === "enlarge") enlargeCrop();
  else { const el = $(b.dataset.goto); el.scrollIntoView({ block: "center" }); el.focus(); }
});
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
// Blindfold mode reads the exported document (with staff edits), so it opens on the preview's HTML.
$("blindfold-btn").hidden = !(window.Blindfold && Blindfold.supported);
$("blindfold-btn").addEventListener("click", async () => {
  const btn = $("blindfold-btn");
  if (btn.getAttribute("aria-disabled") === "true" || !state.docId) return;
  try {
    const res = await fetch(`${docPath(state.docId)}/preview`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const doc = new DOMParser().parseFromString(await res.text(), "text/html");
    const f = current();  // start at the selected figure's block; "Start from the top" is in the dialog
    Blindfold.open(Blindfold.fromDocument(doc), btn, f ? { region: f.region_id, page: f.page } : null);
  } catch (e) {
    banner(`Could not open Blindfold mode: ${e.message}`, "error");
  }
});
window.addEventListener("beforeunload", (e) => { if (state.dirty) e.preventDefault(); });
$("filter-review").checked = store("filter") !== "all";  // on by default
showReviewer();

loadConfig().catch(() => {});  // without it the page works as before (no demo note)
loadDocs().catch((e) => banner(`Could not load documents: ${e.message}`, "error"));
