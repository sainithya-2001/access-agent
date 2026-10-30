"""Course portal end to end (app/portal.py, /portal.html), on the test clip.

  python scripts/test_portal.py

Starts the API with PORTAL_WATCH=1 on a temporary portal folder and index, then copies the test clip
into <portal>/stats-101/ in two halves, a poll apart: the watcher must leave the half-copied file alone,
then process it. The recording's results come from the cache that scripts/run_lecture.py filled; the
model APIs point at a closed port, so a cache miss fails instead of spending. Then, in a browser:
the student page shows "New materials ready (1)" with a link; opening it shows the notes page from
/lectures/ (the video seeks: range requests); back on the portal nothing is new; axe at 0 on the portal
in both states.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from scripts.check_a11y import run_axe  # noqa: E402

CLIP = ROOT / "data" / "lectures" / "test_lecture.mp4"
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def portal_state(base: str) -> list:
    return json.load(urllib.request.urlopen(base + "/api/portal", timeout=10))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="portal-"))
    course = tmp / "portal" / "stats-101"
    course.mkdir(parents=True)
    (course / "course.json").write_text(json.dumps({"title": "Introductory Statistics"}), encoding="utf-8")
    port, dead = free_port(), f"http://127.0.0.1:{free_port()}/v1"
    env = {**os.environ, "PORTAL_WATCH": "1", "PORTAL_INTERVAL": "1", "PORTAL_DIR": str(tmp / "portal"),
           "PORTAL_INDEX": str(tmp / "index.json"), "MODEL_API_KEY": "fake", "BASETEN_API_KEY": "fake",
           "MODEL_API_BASE": dead, "BASETEN_API_BASE": dead}
    env.pop("DEMO_MODE", None)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api:app", "--port", str(port)], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                portal_state(base)
                break
            except OSError:
                time.sleep(0.2)
        print("Watcher")
        data = CLIP.read_bytes()
        target = course / "lecture-01.mp4"
        with open(target, "wb") as f:
            f.write(data[: len(data) // 2])
        time.sleep(1.2)
        with open(target, "ab") as f:  # the second half lands while the watcher polls
            f.write(data[len(data) // 2:])
        mid = portal_state(base)[0]["materials"]
        check(not mid or mid[0]["status"] != "failed", f"half-copied file not processed as is ({mid and mid[0]['status']})")
        state = None
        for _ in range(240):
            state = portal_state(base)
            m = state[0]["materials"]
            if m and m[0]["status"] in ("ready", "failed"):
                break
            time.sleep(0.5)
        m = state[0]["materials"][0]
        check(state[0]["title"] == "Introductory Statistics", f"course listed: {state[0]['title']}")
        check(m["status"] == "ready" and m["url"], f"lecture processed: {m['status']} {m.get('url') or ''}")

        print("Student page")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page()
            errors = []
            pg.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
            pg.goto(base + "/portal.html")
            pg.wait_for_function("document.getElementById('inbox-h').textContent.startsWith('New')")
            check(pg.inner_text("#inbox-h") == "New materials ready (1)", f"inbox: {pg.inner_text('#inbox-h')}")
            check(pg.locator(".badge").count() == 1, "the lecture is marked New in its course")
            r = run_axe(pg)
            check(not r["violations"], f"axe on the portal (new materials): {[v['id'] for v in r['violations']] or 0} violations")
            pg.click("#inbox-list a")
            pg.wait_for_selector("#lecture-video")
            check(pg.title().endswith("| Lecture notes"), f"notes page opens: {pg.title()}")
            pg.wait_for_function("document.getElementById('lecture-video').readyState >= 1")
            pg.click("#seg-8 .play-from")
            pg.wait_for_timeout(600)
            t = pg.evaluate("document.getElementById('lecture-video').currentTime")
            want = float(pg.get_attribute("#seg-8 .play-from", "data-t"))
            check(abs(t - want) < 1.5, f"'Play from' seeks the video: {t:.1f} s (segment at {want:.1f} s)")
            cues = pg.evaluate("(() => { const tr = document.getElementById('lecture-video').textTracks[0]; tr.mode = 'hidden';"
                               " return new Promise(r => setTimeout(() => r(tr.cues ? tr.cues.length : 0), 800)); })()")
            check(cues > 0, f"captions load from /lectures/: {cues} cues")
            pg.goto(base + "/portal.html")
            pg.wait_for_function("!document.getElementById('inbox-h').textContent.startsWith('Loading')")
            check(pg.inner_text("#inbox-h") == "No new materials since your last visit", f"after opening: {pg.inner_text('#inbox-h')}")
            r = run_axe(pg)
            check(not r["violations"], f"axe on the portal (nothing new): {[v['id'] for v in r['violations']] or 0} violations")
            pg.set_viewport_size({"width": 375, "height": 800})
            check(pg.evaluate("document.documentElement.scrollWidth <= 375"), "no sideways scroll at 375 px")
            br.close()
        check(not errors, f"no failed requests {errors[:3]}")
    finally:
        srv.terminate()
        srv.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
