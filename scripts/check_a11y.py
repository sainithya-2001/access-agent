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
  speak(u) { this.u = u; setTimeout(() => { if (this.u === u && u.onend) u.onend(); }, 200); } } });
window.SpeechSynthesisUtterance = function (t) { this.text = t; };
"""


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
        browser.close()
    n += report("exported HTML", result)
    (path.parent / "a11y_report.json").write_text(json.dumps(
        {"violations": result["violations"], "incomplete": result["incomplete"]}, indent=2), encoding="utf-8")
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
            page.add_init_script("try { localStorage.setItem('reviewer', 'a11y check'); "
                                 "localStorage.setItem('filter', 'all'); } catch (e) {}")
            page.goto(url)
            page.wait_for_selector(".queue-item", timeout=15000)
            page.wait_for_load_state("networkidle")
            print(f"axe-core review screen at {url}")

            total += report("review screen", run_axe(page))
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
            page.click("#blindfold-btn")
            page.wait_for_selector("dialog.bf[open]")
            total += report("Blindfold mode", run_axe(page))
            page.keyboard.press("Escape")
            page.uncheck("#filter-review", force=True)

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
