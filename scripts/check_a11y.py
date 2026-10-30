"""Run axe-core in headless Chromium and list the violations.

Usage:
  python scripts/check_a11y.py                          # newest output/*/accessible.html
  python scripts/check_a11y.py output/<doc>/accessible.html
  python scripts/check_a11y.py --review                 # the staff review screen (starts the API)
  python scripts/check_a11y.py --review http://localhost:8000/

Setup (once): pip install playwright && python -m playwright install chromium && npm install
Rules: WCAG 2.0/2.1/2.2 A and AA, plus axe best practices (heading order, landmarks, ...).
The review screen is checked in each state staff see (queue, filter on and off, technical details,
an unsaved edit, the crop tool, reject form, name form, dialogs), and with a keyboard-only walk: skip
link first, J/K move the selection, the action buttons are reachable with Tab, every focused element
shows a visible focus indicator, the status line's button reaches its field, and the crop box moves
and resizes with the arrow keys. Nothing is approved, saved or re-run.
Exit code 1 if there are violations.
"""

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

AXE = ROOT / "node_modules" / "axe-core" / "axe.min.js"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


def run_axe(page) -> dict:
    if not page.evaluate("typeof axe !== 'undefined'"):
        page.add_script_tag(path=str(AXE))
    return page.evaluate(
        "async (tags) => await axe.run(document, {runOnly: {type: 'tag', values: tags}})", TAGS)


def report(label: str, result: dict) -> int:
    violations = result["violations"]
    print(f"\n== {label}: {len(result['passes'])} rules passed, {len(violations)} with violations, "
          f"{len(result['incomplete'])} need a manual check")
    for v in violations:
        print(f"[{v['impact']}] {v['id']}: {v['help']} ({len(v['nodes'])} element(s))")
        print(f"  {v['helpUrl']}")
        for n in v["nodes"][:5]:
            print(f"  - {', '.join(map(str, n['target']))}: {n['failureSummary'].splitlines()[-1].strip()}")
    for v in result["incomplete"]:
        targets = ", ".join(", ".join(map(str, n["target"])) for n in v["nodes"][:3])
        print(f"[manual check] {v['id']}: {v['help']} ({len(v['nodes'])} element(s): {targets})")
    return len(violations)


# Blindfold mode is checked with a silent stand-in for the browser's speech engine.
SILENT_SPEECH = """
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  cancel() { this.u = null; }, pause() {}, resume() {}, getVoices() { return []; },
  speak(u) { this.u = u;
    if (u.onboundary) setTimeout(() => { if (this.u === u) u.onboundary({ name: 'word', charIndex: 0, charLength: 0 }); }, 20);
    setTimeout(() => { if (this.u === u && u.onend) u.onend(); }, 200); } } });
window.SpeechSynthesisUtterance = function (t) { this.text = t; };
"""


# "Hear the chart" is checked with a silent Web Audio stand-in that records each tone and click.
FAKE_AUDIO = """
window.__tones = []; window.__clicks = 0;
window.AudioContext = function () {
  const t0 = performance.now();
  const node = () => ({ connect(n) { return n; }, start() {}, stop() {} });
  const param = () => ({ value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} });
  return { state: 'running', sampleRate: 8000, destination: {}, resume: async () => {},
    get currentTime() { return (performance.now() - t0) / 1000; },
    createOscillator() { const o = Object.assign(node(), { frequency: param() });
      o.start = (at) => window.__tones.push(o.frequency.value); return o; },
    createGain() { return Object.assign(node(), { gain: param() }); },
    createBiquadFilter() { return Object.assign(node(), { frequency: param() }); },
    createBuffer(c, n) { return { getChannelData: () => new Float32Array(n) }; },
    createBufferSource() { const s = node(); s.start = () => window.__clicks++; return s; } };
};
"""


def hear_keys(page, scope: str) -> list[str]:
    """Play the first "Hear the chart" button in scope from the keyboard: one tone and one click per
    data point, higher values higher pitch, each series named first; Esc stops."""
    problems = []
    btn = page.locator(f"{scope} .hear-chart-btn").first
    if not btn.is_visible():
        return [f"Hear the chart is not shown in {scope}"]
    sound = json.loads(btn.get_attribute("data-sound"))
    page.evaluate("""() => { window.__tones = []; window.__clicks = 0; speechSynthesis.said = [];
      if (speechSynthesis.recorded) return;
      const s = speechSynthesis.speak.bind(speechSynthesis);
      speechSynthesis.speak = (u) => { speechSynthesis.said.push(u.text); s(u); };
      speechSynthesis.recorded = true; }""")
    btn.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    if btn.inner_text().split(":")[0].strip() != "Stop":
        problems.append("Hear the chart did not switch to Stop while playing")
    page.wait_for_function("b => b.querySelector('.hear-chart-label').textContent === 'Hear the chart'",
                           arg=btn.element_handle(), timeout=30000)
    pts = [p for s in sound["series"] for p in s["points"]]
    tones = page.evaluate("window.__tones")
    if len(tones) != len(pts) or page.evaluate("window.__clicks") != len(pts):
        problems.append(f"expected {len(pts)} tones and clicks, got {len(tones)} tones")
    else:
        for (a, fa), (b, fb) in zip(zip(pts, tones), zip(pts[1:], tones[1:])):
            if (a["value"] < b["value"]) != (fa < fb) and a["value"] != b["value"]:
                problems.append(f"pitch does not follow the values at {a['label']} → {b['label']}")
                break
    said = page.evaluate("speechSynthesis.said")
    if said != [s["name"] for s in sound["series"]]:
        problems.append(f"series names spoken {said}, expected {[s['name'] for s in sound['series']]}")
    page.keyboard.press("Enter")  # play again, then stop with Esc
    page.wait_for_timeout(150)
    page.keyboard.press("Escape")
    if btn.inner_text().split(":")[0].strip() != "Hear the chart":
        problems.append("Esc did not stop Hear the chart")
    return problems


def blindfold_keys(page, opener: str) -> list[str]:
    """Open Blindfold mode from the keyboard, step through blocks, close with Esc."""
    problems = []
    page.locator(opener).focus()
    page.keyboard.press("Enter")
    try:
        page.wait_for_selector("dialog.bf[open]", timeout=5000)
    except Exception:
        return [f"{opener} did not open Blindfold mode"]
    if "bf-play" not in (page.evaluate("document.activeElement.className") or ""):
        problems.append("Blindfold mode did not focus Play/Pause")
    page.keyboard.press("Space")  # pause (Space on the focused button)
    if page.inner_text(".bf-play") != "Play":
        problems.append("Space did not pause Blindfold mode")
    pos = page.inner_text(".bf-pos")
    page.keyboard.press("Tab")  # to Next
    page.keyboard.press("Enter")
    if page.inner_text(".bf-pos") == pos:
        problems.append("Next did not move to the next block")
    page.keyboard.press("Escape")
    if page.locator("dialog.bf").is_visible():
        problems.append("Esc did not close Blindfold mode")
    elif page.evaluate("'#' + document.activeElement.id") != opener:
        problems.append(f"focus did not return to {opener} after Blindfold mode")
    return problems


def blindfold_at_figure(page) -> tuple[int, list[str]]:
    """Blindfold opened with a figure selected starts at that figure's first block (else the first
    block on its page); Start from the top goes to block 1. Axe in both states."""
    n, problems = 0, []
    page.locator(".queue-item").last.click()  # a figure past the start of the document
    expect = page.evaluate("""async () => {
        const f = current();
        const res = await fetch(`${docPath(state.docId)}/preview`);
        const doc = new DOMParser().parseFromString(await res.text(), "text/html");
        const blocks = Blindfold.accessibleBlocks(doc.querySelector("main"));
        let i = blocks.findIndex((b) => b.region === f.region_id);
        if (i < 0) i = Math.max(0, blocks.findIndex((b) => b.page >= f.page));
        return { i, n: blocks.length, fig: f.region_id };
    }""")
    page.click("#blindfold-btn")
    page.wait_for_selector("dialog.bf[open]")
    page.click(".bf-play")  # pause, so the position stays put
    pos = page.inner_text(".bf-pos")
    if f"block {expect['i'] + 1} of" not in pos:
        problems.append(f"opened at '{pos}', expected block {expect['i'] + 1} (figure {expect['fig']})")
    if expect["i"] == 0:
        problems.append("the selected figure is the first block: start-at-figure not really tested")
    n += report("Blindfold mode at the selected figure", run_axe(page))
    page.click(".bf-top-btn")
    if "block 1 of" not in page.inner_text(".bf-pos"):
        problems.append(f"Start from the top went to '{page.inner_text('.bf-pos')}', not block 1")
    n += report("Blindfold mode after Start from the top", run_axe(page))
    page.keyboard.press("Escape")
    return n, problems


PROFILES = ["screen-reader", "low-vision", "reading", "keyboard"]


def _content(page) -> str:
    """Main's accessibility tree, minus the profile-only controls (Zoom figure buttons)."""
    snap = page.locator("main").aria_snapshot()
    return "\n".join(line for line in snap.splitlines() if "Zoom figure" not in line)


def check_profiles(page) -> int:
    """Each reading profile: axe at 0, the same content as Screen reader, no sideways scroll on a phone,
    and its own controls (zoom dialog, read-aloud bar, focus outlines) working from the keyboard."""
    n = 0
    base = None
    for prof in PROFILES:
        page.set_viewport_size({"width": 1280, "height": 900})
        page.check(f".profile-bar input[value={prof}]")
        problems = []
        if page.evaluate("document.documentElement.dataset.profile || 'screen-reader'") != prof:
            problems.append("profile not applied")
        content = _content(page)
        if base is None:
            base = content
        elif content != base:
            problems.append("content differs from the Screen reader profile")
        n += report(f"exported HTML, profile {prof}", run_axe(page))

        if prof == "low-vision":
            btn = page.locator(".zoom-btn").first
            btn.focus()
            page.keyboard.press("Enter")
            try:
                page.wait_for_selector("dialog.zoom-dialog[open]", timeout=3000)
                n += report("exported HTML, profile low-vision, zoom dialog", run_axe(page))
                before = page.inner_text(".zoom-level")
                page.keyboard.press("+")
                if page.inner_text(".zoom-level") == before:
                    problems.append("+ did not zoom in")
                page.keyboard.press("Escape")
                if not page.evaluate("document.activeElement.classList.contains('zoom-btn')"):
                    problems.append("focus did not return to Zoom figure")
            except Exception:
                problems.append("Zoom figure did not open the zoom dialog")
        elif prof == "reading":
            page.locator(".ra-play").focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(120)
            if not page.locator(".ra-current").count():
                problems.append("read-aloud did not mark the current block")
            if not page.evaluate("CSS.highlights && CSS.highlights.has('ra-word')"):
                problems.append("read-aloud did not highlight the current word")
            n += report("exported HTML, profile reading, reading aloud", run_axe(page))
            page.keyboard.press("Enter")  # pause
            if page.inner_text(".ra-play") != "Play":
                problems.append("Play/Pause did not pause")
        elif prof == "keyboard":
            page.locator(".profile-bar input[value=keyboard]").focus()
            page.keyboard.press("Tab")
            width = page.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)")
            if width < 4:
                problems.append(f"focus outline is {width}px, expected 4px or more")

        page.set_viewport_size({"width": 375, "height": 800})
        if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            problems.append("page scrolls sideways at 375px")
        print(f"\n== exported HTML, profile {prof} behaviour: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        n += len(problems)
    page.check(".profile-bar input[value=screen-reader]")
    page.set_viewport_size({"width": 1280, "height": 900})
    return n


# ---------- exported HTML ----------

def check_export(arg: str | None) -> int:
    if arg:
        path = Path(arg)
    else:
        found = sorted(ROOT.glob("output/*/accessible.html"), key=lambda p: p.stat().st_mtime)
        if not found:
            sys.exit("No output/*/accessible.html yet. Run scripts/run_export.py first.")
        path = found[-1]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.add_init_script(SILENT_SPEECH)
        page.add_init_script(FAKE_AUDIO)
        page.goto(path.resolve().as_uri())
        result = run_axe(page)
        print(f"axe-core {page.evaluate('axe.version')} on {path}")
        n = 0
        if page.locator("#blindfold-open").count():
            page.click("#blindfold-open")
            n += report("exported HTML, Blindfold mode (accessible)", run_axe(page))
            page.check("dialog.bf input[value=original]")
            n += report("exported HTML, Blindfold mode (original)", run_axe(page))
            page.keyboard.press("Escape")
            problems = blindfold_keys(page, "#blindfold-open")
            print(f"\n== exported HTML, Blindfold mode keyboard: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            n += len(problems)
        if page.locator(".profile-bar").count():
            n += check_profiles(page)
        if page.locator(".hear-chart-btn").count():
            page.locator(".hear-chart-btn").first.click()
            n += report("exported HTML, Hear the chart playing", run_axe(page))
            page.keyboard.press("Escape")
            problems = []
            for fid in page.eval_on_selector_all(".figure-block:has(.hear-chart-btn)", "els => els.map(e => e.id)"):
                problems += [f"{fid}: {p}" for p in hear_keys(page, f"#{fid}")]
            print(f"\n== exported HTML, Hear the chart keyboard ({page.locator('.hear-chart-btn').count()} charts): "
                  f"{len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            n += len(problems)
        browser.close()
    n += report("exported HTML", result)
    (path.parent / "a11y_report.json").write_text(json.dumps(
        {"violations": result["violations"], "incomplete": result["incomplete"]}, indent=2,
        default=str), encoding="utf-8")  # an axe rule that errored puts an Error object in "incomplete"
    return n


# ---------- review screen ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server() -> tuple[subprocess.Popen, str]:
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api:app", "--port", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "api/documents", timeout=1)
            return proc, url
        except OSError:
            time.sleep(0.5)
    proc.kill()
    sys.exit("The API server did not start (try: uvicorn app.api:app)")


FOCUS_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const s = getComputedStyle(el);
  const t = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2;
  // a visually hidden input shows its focus on the switch track next to it
  const track = el.matches('.switch input') && getComputedStyle(el.nextElementSibling).outlineStyle !== 'none';
  return {tag: el.tagName, id: el.id, text: (el.getAttribute('aria-label') || el.textContent || el.value || '').trim().slice(0, 50),
          visible: t || track};
}"""


def keyboard_walk(page) -> list[str]:
    """Problems found using the screen with the keyboard only."""
    problems = []
    page.keyboard.press("Tab")
    first = page.evaluate(FOCUS_JS)
    if not first or "skip-link" not in (page.evaluate("document.activeElement.className") or ""):
        problems.append(f"first Tab stop is {first}, expected the skip link")

    # Tab through everything; the approve button must be reachable and every stop visibly focused.
    seen, reached = [], False
    for _ in range(200):
        page.keyboard.press("Tab")
        f = page.evaluate(FOCUS_JS)
        if not f:
            continue
        key = (f["tag"], f["id"], f["text"])
        if seen and key == seen[0]:
            break  # wrapped around
        seen.append(key)
        reached |= f["id"] == "approve-btn"
        if not f["visible"]:
            problems.append(f"no visible focus indicator on {f['tag']} '{f['text']}'")
    if not reached:
        problems.append("the Approve button is not reachable with Tab")

    # J / K move the selection (focus must not be in a text field).
    page.locator(".queue-item").first.focus()
    before = page.inner_text("#detail-heading")
    page.keyboard.press("j")
    after = page.inner_text("#detail-heading")
    if page.locator(".queue-item").count() > 1 and before == after:
        problems.append("J did not move to the next figure")
    page.keyboard.press("k")
    if page.inner_text("#detail-heading") != before:
        problems.append("K did not move back to the previous figure")
    # The status line's button (if any) moves focus to the field it names.
    go = page.locator(".status-line [data-goto]")
    if go.count():
        target = go.first.get_attribute("data-goto")
        go.first.focus()
        page.keyboard.press("Enter")
        if page.evaluate("document.activeElement.id") != target:
            problems.append(f"the status line button did not move focus to #{target}")
    page.locator(".queue-item").first.focus()
    # Typing makes Save available; Ctrl+S is not sent (it would save).
    page.keyboard.press("e")
    page.keyboard.type(" x")
    if page.locator("#save-btn").is_disabled():
        problems.append("Save did not become available after an edit")
    page.click("#discard-btn")
    # The crop tool: open it from the keyboard, move and resize the box with the arrow keys, Esc leaves.
    page.locator(".crop [data-action=crop]").focus()
    page.keyboard.press("Enter")
    if page.evaluate("document.activeElement.id") != "crop-rect":
        problems.append("Adjust crop did not focus the crop box")
    else:
        box = page.locator("#crop-rect").bounding_box()
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Shift+ArrowDown")
        moved = page.locator("#crop-rect").bounding_box()
        if not (moved["x"] > box["x"] and moved["height"] > box["height"]):
            problems.append("arrow keys did not move / Shift+arrows did not resize the crop box")
        # + / - zoom the page while the crop box has focus.
        width = page.locator("#page-frame").bounding_box()["width"]
        page.keyboard.press("+")
        if not page.locator("#page-frame").bounding_box()["width"] > width:
            problems.append("+ did not zoom the page while adjusting the crop")
        page.keyboard.press("-")
        page.keyboard.press("Escape")
        if not page.locator("#crop-rect").is_hidden():
            problems.append("Esc did not close the crop tool")
        if abs(page.locator("#page-frame").bounding_box()["width"] - width) > 1:
            problems.append("closing the crop tool did not reset the zoom")
    # Enlarge opens the full crop in a dialog; Esc closes it and focus goes back to the button.
    page.locator(".crop button[data-action=enlarge]").focus()
    page.keyboard.press("Enter")
    if not page.locator("#crop-dialog").is_visible():
        problems.append("Enlarge did not open the crop dialog")
    else:
        page.keyboard.press("Escape")
        if page.locator("#crop-dialog").is_visible():
            problems.append("Esc did not close the crop dialog")
        elif page.evaluate("document.activeElement.dataset.action") != "enlarge":
            problems.append("focus did not return to Enlarge after closing the crop dialog")
    page.locator(".queue-item").first.focus()
    # E jumps into the alt text box, Esc leaves it again.
    page.keyboard.press("e")
    if page.evaluate("document.activeElement.id") != "alt-text":
        problems.append("E did not focus the alt text box")
    page.keyboard.press("Escape")
    if page.evaluate("document.activeElement.id") == "alt-text":
        problems.append("Esc did not leave the alt text box")
    return problems


def header_checks(page) -> list[str]:
    """At 1440px and wider the header is one line and the legend one row; the document's full name
    shows on keyboard focus (Esc hides it); an empty filtered queue offers Show all N figures."""
    problems = []
    one_line = """(sel) => { const r = [...document.querySelectorAll(sel)]
      .filter(e => e.offsetParent && e.getBoundingClientRect().width > 2).map(e => e.getBoundingClientRect());
      return r.every(a => r.every(b => a.top < b.bottom - 1 && b.top < a.bottom - 1)); }"""
    size = page.viewport_size
    for w in (1440, 1920):
        page.set_viewport_size({"width": w, "height": size["height"]})
        if not page.evaluate(one_line, ".topbar > *, .topbar-actions > *"):
            problems.append(f"header wraps at {w}px")
        if not page.evaluate(one_line, "#legend > span"):
            problems.append(f"legend wraps at {w}px")
    page.set_viewport_size(size)

    page.locator("#doc-select").focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")  # keyboard focus, so :focus-visible applies
    tip = page.locator("#doc-full")
    want = page.evaluate("document.querySelector('#doc-select').selectedOptions[0]?.title || ''")
    if not tip.is_visible() or tip.inner_text().strip() != want.strip() or not want:
        problems.append("the full document name does not show on focus")
    else:
        total = report("document name shown on focus", run_axe(page))
        problems += ["axe violations with the document name shown"] * bool(total)
        page.keyboard.press("Escape")
        if tip.is_visible():
            problems.append("Esc did not hide the document name")

    page.check("#filter-review", force=True)
    if page.locator(".queue-item").count() == 0:
        n = page.evaluate("state.figs.length")
        btn = page.locator("#show-all")
        if not btn.is_visible() or btn.inner_text() != f"Show all {n} figures":
            problems.append(f"empty queue: no 'Show all {n} figures' button")
        else:
            total = report("empty queue (needs-review filter on)", run_axe(page))
            problems += ["axe violations on the empty queue"] * bool(total)
            btn.focus()
            page.keyboard.press("Enter")
            if page.locator(".queue-item").count() != n or page.is_checked("#filter-review"):
                problems.append("Show all did not show every figure")
            elif not page.evaluate("document.activeElement.classList.contains('queue-item')"):
                problems.append("Show all did not move focus to the queue")
    else:
        print("(empty-queue state not checked: some figures still need review)")
    page.uncheck("#filter-review", force=True)
    return problems


def check_review(url: str | None) -> int:
    from playwright.sync_api import sync_playwright

    proc = None
    if not url:
        proc, url = _start_server()
    total = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.add_init_script(SILENT_SPEECH)
            page.add_init_script(FAKE_AUDIO)
            page.add_init_script("try { localStorage.setItem('reviewer', 'a11y check'); "
                                 "localStorage.setItem('filter', 'all'); } catch (e) {}")
            page.goto(url)
            page.wait_for_selector(".queue-item", timeout=15000)
            page.wait_for_load_state("networkidle")
            print(f"axe-core review screen at {url}")

            total += report("review screen", run_axe(page))
            problems = header_checks(page)
            print(f"\n== review screen, header, document name and empty queue: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            total += len(problems)
            # A figure that needs review shows the reason, issues and a longer trace.
            item = page.locator(".queue-item .dot.needs_review").first
            if item.count():
                item.click()
                total += report("figure needing review", run_axe(page))
            page.click("#tech summary")
            total += report("technical details open", run_axe(page))
            page.click("#tech summary")
            page.fill("#alt-text", page.input_value("#alt-text") + " (edited)")
            total += report("unsaved edit", run_axe(page))
            page.click("#discard-btn")
            page.click(".crop button[data-action=enlarge]")
            total += report("enlarged crop dialog", run_axe(page))
            page.click("#crop-dialog-close")
            page.click(".crop [data-action=crop]")
            total += report("crop tool open", run_axe(page))
            page.click("#zoom-in")
            page.click("#zoom-in")
            total += report("crop tool zoomed in", run_axe(page))
            page.click("#crop-cancel")
            page.click("#reviewer-change")
            total += report("reviewer name form", run_axe(page))
            page.click("#reviewer-form button[type=submit]")
            page.check("#filter-review", force=True)
            total += report("needs-review filter on", run_axe(page))
            page.click("#reject-btn")
            total += report("reject form open", run_axe(page))
            page.click("#reject-cancel")
            page.click("#upload-open")
            total += report("upload dialog", run_axe(page))
            page.click("#upload-cancel")
            page.click("#shortcuts-open")
            total += report("shortcuts dialog", run_axe(page))
            page.click("#shortcuts-close")
            page.uncheck("#filter-review", force=True)
            problems = []
            for qid in page.eval_on_selector_all(".queue-item", "els => els.map(e => e.dataset.id)"):
                page.click(f'.queue-item[data-id="{qid}"]')
                if page.locator("#detail-body .hear-chart-btn").count():
                    total += report("Hear the chart in the detail panel", run_axe(page))
                    problems = hear_keys(page, "#detail-body")
                    break
            else:
                problems = ["no figure in the queue offers Hear the chart"]
            print(f"\n== review screen, Hear the chart keyboard: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            total += len(problems)
            page.check("#filter-review", force=True)
            page.click("#blindfold-btn")
            page.wait_for_selector("dialog.bf[open]")
            total += report("Blindfold mode", run_axe(page))
            page.keyboard.press("Escape")
            page.uncheck("#filter-review", force=True)
            n, problems = blindfold_at_figure(page)
            print(f"\n== review screen, Blindfold at the selected figure: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            total += n + len(problems)

            page.reload()
            page.wait_for_selector(".queue-item")
            problems = keyboard_walk(page) + blindfold_keys(page, "#blindfold-btn")
            print(f"\n== keyboard only: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
            total += len(problems)
            browser.close()
    finally:
        if proc:
            proc.terminate()
    return total


def main() -> None:
    if not AXE.exists():
        sys.exit("axe-core is missing. Run: npm install")
    args = sys.argv[1:]
    if args and args[0] == "--review":
        n = check_review(args[1] if len(args) > 1 else None)
    else:
        n = check_export(args[0] if args else None)
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
