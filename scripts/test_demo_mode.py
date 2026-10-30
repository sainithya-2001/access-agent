"""DEMO_MODE (app/api.py, DEPLOY.md): the app serves the precomputed demo/ data without model calls,
and uploads and recrops need the passcode.

  python scripts/test_demo_mode.py

Runs the server the way the Docker image does: from a copy of demo/ as the working directory, with
DEMO_MODE=1. The model API base URLs point at a closed local port and the keys are fake, so even a
call that slipped through would cost nothing; the test fails on any line in the call log
(output/llm_calls.jsonl in that directory), cached or not. Checks:
  1. page load in a browser (queue, every figure selected, preview; axe-core on the demo upload
     dialog) and every read endpoint: 200, 0 calls
  2. upload without / with a wrong passcode → 403; recrop without one → 403
  3. passcode upload over 5 pages → 422; a 2-page blank PDF → accepted and processed (no figures, no calls)
  4. without DEMO_PASSCODE: uploads off (config says so, upload → 403)
  5. app.llm with BLOCKED: an uncached call raises before any request; allow_calls() lets it through
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

PASS = "test-passcode"
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, cwd: Path, passcode: str | None):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        dead = f"http://127.0.0.1:{free_port()}/v1"  # nothing listens here
        env = {**os.environ, "DEMO_MODE": "1", "PYTHONPATH": str(ROOT),
               "MODEL_API_KEY": "fake", "BASETEN_API_KEY": "fake",
               "MODEL_API_BASE": dead, "BASETEN_API_BASE": dead}
        env.pop("DEMO_PASSCODE", None)
        if passcode:
            env["DEMO_PASSCODE"] = passcode
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api:app", "--port", str(self.port)],
                                     cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/api/config", timeout=1)
                return
            except OSError:
                time.sleep(0.2)
        raise SystemExit("Server did not start")

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            body = r.read()
        return r.status, body

    def post(self, path: str, body: bytes, ctype: str, passcode: str | None = None):
        headers = {"Content-Type": ctype, **({"X-Demo-Passcode": passcode} if passcode else {})}
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def upload(self, pdf: bytes, pages: str = "", passcode: str | None = None):
        b = uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="pages"\r\n\r\n{pages}\r\n'.encode(),
                 f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="demo-mode-test.pdf"\r\n'
                 f'Content-Type: application/pdf\r\n\r\n'.encode() + pdf + b"\r\n", f"--{b}--\r\n".encode()]
        return self.post("/api/documents", b"".join(parts), f"multipart/form-data; boundary={b}", passcode)

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)


def blank_pdf(n: int) -> bytes:
    doc = fitz.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"Demo mode test, page {i + 1}. No figures here.")
    return doc.tobytes()


def call_log(cwd: Path) -> list[dict]:
    path = cwd / "output" / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def browse(base: str, doc_id: str) -> int:
    """Load the review screen, select every figure, open the preview. Returns figures selected."""
    from playwright.sync_api import sync_playwright

    from scripts.check_a11y import run_axe
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        bad = []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.goto(base + "/")
        page.wait_for_selector(".queue-item")
        page.click("label.switch")  # "Needs review only" is on by default: show every figure
        items = page.locator(".queue-item")
        page.wait_for_function("document.querySelectorAll('.queue-item').length > 2")
        n = items.count()
        for i in range(n):
            items.nth(i).click()
            page.wait_for_timeout(150)
        check(not page.locator("#upload-open").is_hidden(), "browser: Upload PDF button shown (a passcode is set)")
        page.click("#upload-open")
        check(page.locator("#upload-passcode").is_visible() and page.locator("#demo-note").is_visible(),
              "browser: upload dialog shows the demo note and passcode field")
        axe = run_axe(page)
        check(not axe["violations"], f"browser: axe-core on the demo upload dialog: "
                                     f"{[v['id'] for v in axe['violations']] or 0} violations")
        page.click("#upload-cancel")
        page.goto(f"{base}/api/documents/{doc_id}/preview")
        page.wait_for_load_state("networkidle")
        # The course portal, from the review screen's header, and the lecture's notes page
        page.goto(base + "/")
        page.click("#portal-link")
        page.wait_for_url("**/portal.html")
        page.wait_for_selector("main a[href^='/lectures/']")
        axe = run_axe(page)
        check(not axe["violations"], f"browser: header → student inbox; axe-core on it: "
                                     f"{[v['id'] for v in axe['violations']] or 0} violations")
        page.click("main a[href^='/lectures/']")
        page.wait_for_url("**/notes.html")
        page.wait_for_load_state("networkidle")
        ok = page.evaluate("document.querySelector('video').readyState >= 1 && "
                           "document.querySelector('video').textTracks.length === 1")
        axe = run_axe(page)
        check(ok and not axe["violations"], f"browser: lecture notes page, video and captions load; axe-core: "
                                            f"{[v['id'] for v in axe['violations']] or 0} violations")
        browser.close()
    check(not bad, f"browser: no failed requests {bad[:3]}")
    return n


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="demo-mode-"))
    shutil.copytree(ROOT / "demo", tmp, dirs_exist_ok=True)
    print(f"Serving a copy of demo/ from {tmp}")

    srv = Server(tmp, PASS)
    try:
        print("1. Page load and read endpoints")
        cfg = json.loads(srv.get("/api/config")[1])
        check(cfg == {"demo_mode": True, "uploads": True, "max_pages": 5}, f"config {cfg}")
        docs = json.loads(srv.get("/api/documents")[1])
        check(len(docs) == 1 and docs[0]["status"] == "ready", f"{len(docs)} precomputed document(s), ready")
        doc_id = docs[0]["id"]
        q = urllib.request.quote(doc_id)
        n = browse(srv.base, doc_id)
        doc = json.loads(srv.get(f"/api/documents/{q}")[1])
        figs = json.loads(srv.get(f"/api/documents/{q}/figures")[1])
        check(n == len(figs), f"browser: selected all {len(figs)} figures")
        codes = [srv.get(f"/api/documents/{q}/pages/{p['number']}/{k}")[0]
                 for p in doc["page_list"] for k in ("image", "overlay")]
        codes += [srv.get(f"/api/figures/{urllib.request.quote(f['id'])}/crop")[0] for f in figs]
        codes += [srv.get(f"/api/documents/{q}/{k}")[0] for k in ("preview", "export")]
        check(set(codes) == {200}, f"{len(codes)} page images, overlays, crops and exports: all 200")
        st, _ = srv.post(f"/api/figures/{urllib.request.quote(figs[0]['id'])}/review",
                         json.dumps({"action": "approve", "who": "Demo test"}).encode(), "application/json")
        check(st == 200, "staff review (approve) works without a passcode")
        courses = json.loads(srv.get("/api/portal")[1])
        mats = [m for c in courses for m in c["materials"]]
        check(len(courses) == 1 and mats and all(m["status"] == "ready" and m["url"] for m in mats),
              f"portal: {len(courses)} course, {len(mats)} ready lecture(s)")
        codes = [srv.get(m["url"].replace("notes.html", f))[0] for m in mats
                 for f in ("notes.html", "video.mp4", "captions.vtt")]
        check(set(codes) == {200}, f"lecture notes pages, videos and captions: {codes}")
        log = call_log(tmp)
        check(not log, f"no model calls (call log lines: {len(log)})")

        print("2. Passcode required")
        st, body = srv.upload(blank_pdf(2))
        check(st == 403, f"upload without passcode → {st}: {body.get('detail')}")
        st, _ = srv.upload(blank_pdf(2), passcode="wrong")
        check(st == 403, f"upload with a wrong passcode → {st}")
        box = {"who": "Demo test", "x0": 100, "y0": 100, "x1": 300, "y1": 300}
        st, body = srv.post(f"/api/figures/{urllib.request.quote(figs[0]['id'])}/recrop",
                            json.dumps(box).encode(), "application/json")
        check(st == 403, f"recrop without passcode → {st}")

        print("3. Passcode uploads capped at 5 pages")
        st, body = srv.upload(blank_pdf(6), passcode=PASS)
        check(st == 422, f"6-page PDF, no range → {st}: {body.get('detail')}")
        st, _ = srv.upload(blank_pdf(6), pages="1-99999", passcode=PASS)
        check(st == 422, f"6-page PDF, pages 1-99999 → {st}")
        st, body = srv.upload(blank_pdf(2), passcode=PASS)
        check(st == 202, f"2-page PDF with passcode → {st}")
        if st == 202:
            for _ in range(100):
                d = json.loads(srv.get(f"/api/documents/{urllib.request.quote(body['id'])}")[1])
                if d["status"] in ("ready", "error"):
                    break
                time.sleep(0.2)
            check(d["status"] == "ready", f"passcode upload processed: {d['status']} {d.get('error') or ''}")
        log = call_log(tmp)
        check(not log, f"still no model calls (call log lines: {len(log)})")
    finally:
        srv.stop()

    print("4. No DEMO_PASSCODE: uploads off")
    srv = Server(tmp, None)
    try:
        cfg = json.loads(srv.get("/api/config")[1])
        check(cfg["uploads"] is False, f"config {cfg}")
        st, body = srv.upload(blank_pdf(1), passcode=PASS)
        check(st == 403, f"upload with any passcode → {st}: {body.get('detail')}")
    finally:
        srv.stop()

    print("5. The guard in app/llm.py")
    os.environ.update(MODEL_API_KEY="fake", MODEL_API_BASE=f"http://127.0.0.1:{free_port()}/v1")
    os.chdir(tmp)  # its cache and call log, not the repo's
    from app import llm
    llm.BLOCKED = True
    msg = [{"role": "user", "content": f"demo mode test {uuid.uuid4()}"}]
    try:
        llm.chat(msg, backend="meta", step="demo_test")
        check(False, "blocked call raised")
    except llm.ModelCallsBlocked:
        check(True, "uncached call while blocked raises ModelCallsBlocked")
    try:
        with llm.allow_calls():
            llm.chat(msg, backend="meta", step="demo_test")
    except llm.ModelCallsBlocked:
        check(False, "allow_calls() lets a call through")
    except Exception as e:  # the dead port: the call was attempted, which is the point
        check(True, f"inside allow_calls() the call goes out ({e.__class__.__name__}, to a closed port)")
    os.chdir(ROOT)
    shutil.rmtree(tmp, ignore_errors=True)
    (ROOT / "data" / "uploads" / "demo-mode-test.pdf").unlink(missing_ok=True)  # from check 3

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
