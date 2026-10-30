/* Student portal (app/portal.py, /api/portal): courses and their lecture materials, with an inbox of
   "New materials ready". New = ready and not yet opened or marked seen in this browser (localStorage
   "portal-seen": the files' keys). Checks for new materials every 30 s and announces them. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const keyOf = (c, m) => `${c.id}/${m.file}`;
  let known = null;  // keys of ready materials at the last load: new arrivals are announced

  function seen() { try { return new Set(JSON.parse(localStorage.getItem("portal-seen") || "[]")); } catch (_) { return new Set(); } }
  function saveSeen(set) { try { localStorage.setItem("portal-seen", JSON.stringify([...set])); } catch (_) { /* private mode */ } }
  function markSeen(keys) { const s = seen(); keys.forEach((k) => s.add(k)); saveSeen(s); }

  function minutes(sec) {
    if (!sec) return "";
    const m = Math.floor(sec / 60), s = Math.round(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function render(courses) {
    const s = seen();
    const fresh = [];
    $("courses").innerHTML = courses.map((c) => {
      const items = c.materials.map((m) => {
        const k = keyOf(c, m);
        if (m.status === "ready") {
          const isNew = !s.has(k);
          if (isNew) fresh.push({ c, m, k });
          return `<li><a class="m-title" href="${esc(m.url)}" data-key="${esc(k)}">${esc(m.title)}</a>`
            + (isNew ? '<span class="badge">New</span>' : "")
            + `<span class="m-meta">Lecture notes, slides, transcript and captions · ${esc(minutes(m.duration))} recording · ready ${esc(when(m.ready_at))}</span></li>`;
        }
        if (m.status === "failed") {
          return `<li><span class="m-title">${esc(m.title)}</span><span class="m-meta status-failed">Not available yet: staff have been notified.</span></li>`;
        }
        return `<li><span class="m-title">${esc(m.title)}</span><span class="m-meta status-processing">Being prepared: captions, notes and figure descriptions are on the way.</span></li>`;
      }).join("");
      return `<section aria-labelledby="course-${esc(c.id)}"><h2 id="course-${esc(c.id)}">${esc(c.title)}</h2>`
        + (items ? `<ul class="materials">${items}</ul>` : '<p class="none">No materials yet.</p>') + "</section>";
    }).join("") || '<p class="none">No courses yet.</p>';

    const box = $("inbox");
    box.classList.toggle("empty", !fresh.length);
    $("inbox-h").textContent = fresh.length ? `New materials ready (${fresh.length})` : "No new materials since your last visit";
    $("inbox-list").hidden = !fresh.length;
    $("inbox-list").innerHTML = fresh.map(({ c, m, k }) =>
      `<li><a href="${esc(m.url)}" data-key="${esc(k)}">${esc(m.title)}</a> · ${esc(c.title)}</li>`).join("");
    $("mark-read").hidden = !fresh.length;
    $("mark-read").dataset.keys = JSON.stringify(fresh.map((f) => f.k));

    const ready = courses.flatMap((c) => c.materials.filter((m) => m.status === "ready").map((m) => ({ k: keyOf(c, m), m })));
    if (known) {
      const arrived = ready.filter((x) => !known.has(x.k));
      if (arrived.length) announce(`New materials ready: ${arrived.map((x) => x.m.title).join(", ")}`);
    }
    known = new Set(ready.map((x) => x.k));
  }

  function announce(msg) { const a = $("announcer"); a.textContent = ""; setTimeout(() => { a.textContent = msg; }, 30); }

  async function load() {
    try {
      const r = await fetch("/api/portal");
      if (!r.ok) throw new Error(r.statusText);
      render(await r.json());
    } catch (e) {
      $("inbox-h").textContent = "Could not load your courses. Try again in a moment.";
    }
  }

  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-key]");
    if (a) markSeen([a.dataset.key]);
  });
  $("mark-read").addEventListener("click", () => {
    markSeen(JSON.parse($("mark-read").dataset.keys || "[]"));
    load().then(() => { $("inbox-h").setAttribute("tabindex", "-1"); $("inbox-h").focus(); });
  });
  load();
  setInterval(load, 30000);
})();
