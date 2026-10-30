"""Precomputed data for the public demo (DEMO_MODE, DEPLOY.md): copy a review database and only the
files it uses into demo/, laid out as the app's working directory, so the Docker image copies demo/
to /app and serves it as is.

Usage:
  python scripts/make_demo_bundle.py --db output/review.db
  python scripts/make_demo_bundle.py --db output/scratch.db --out demo

demo/output/review.db                       the database, paths rewritten relative with "/" (Linux)
demo/output/<pdf_id>/{pages,crops,overlays,images}/...   only files the database refers to
demo/data/samples/<name>.pdf                the source PDF trimmed to the document's pages: every other
                                            page blank, same page count and outline, so page numbers,
                                            the export's chapter title and a passcode recrop still work
demo/output/lectures/<id>/                  each --lecture's student page as built (notes.html: figures
                                            inline; video.mp4, captions.vtt, lecture.json)
demo/output/portal/index.json               the course portal listing those lectures (/portal.html)

A database with staff edits is refused (build a clean one with scripts/reset_review_db.py --db ...),
unless --keep-edits. No model calls.

Lectures (default: the test lecture, scripts/make_test_lecture.py) go into one course, --course, as
ready materials; the portal watcher never runs in DEMO_MODE, so the index is served as written.
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_LECTURE = "0d05f0e901c0"  # data/lectures/test_lecture.mp4 with its slides PDF
sys.stdout.reconfigure(encoding="utf-8")

import pymupdf as fitz  # noqa: E402


def rel(path: str) -> str:
    """'output\\abc\\pages\\page_084.png' or an absolute path under ROOT → 'output/abc/pages/page_084.png'."""
    p = Path(path.replace("\\", "/"))
    if p.is_absolute():
        p = p.relative_to(ROOT)
    return p.as_posix()


class Bundle:
    def __init__(self, out: Path):
        self.out = out
        self.files: set[str] = set()

    def file(self, path: str) -> str:
        """Rewrite one path and remember the file for copying."""
        r = rel(path)
        self.files.add(r)
        return r

    def region(self, reg: dict) -> dict:
        reg["crop_path"] = self.file(reg["crop_path"])
        return reg

    def copy(self) -> int:
        size = 0
        for r in sorted(self.files):
            src, dst = ROOT / r, self.out / r
            if not src.exists():
                raise SystemExit(f"Missing file referenced by the database: {r}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            size += src.stat().st_size
        return size


def trimmed_pdf(src: Path, keep: set[int], dst: Path) -> None:
    pdf = fitz.open(src)
    new = fitz.open()
    for i, page in enumerate(pdf):
        if i + 1 in keep:
            new.insert_pdf(pdf, from_page=i, to_page=i)
        else:
            new.new_page(width=page.rect.width, height=page.rect.height)
    new.set_toc(pdf.get_toc())
    new.set_metadata(pdf.metadata)
    dst.parent.mkdir(parents=True, exist_ok=True)
    new.save(dst, garbage=4, deflate=True)


LECTURE_FILES = ("notes.html", "video.mp4", "captions.vtt", "lecture.json")


def bundle_lectures(ids: list[str], course: str, course_title: str, out: Path) -> int:
    """Copy each lecture's student page and write the portal index. Returns bytes copied."""
    size, materials = 0, {}
    for lid in ids:
        src = ROOT / "output" / "lectures" / lid
        lec = json.loads((src / "lecture.json").read_text(encoding="utf-8"))
        for name in LECTURE_FILES:
            dst = out / "output" / "lectures" / lid / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / name, dst)
            size += dst.stat().st_size
        video = Path(lec["video"]).name
        ready = datetime.fromtimestamp((src / "notes.html").stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        materials[f"{course}/{video}"] = {
            "course": course, "course_title": course_title, "file": video, "stamp": None, "status": "ready",
            "added": ready, "lecture_id": lid, "title": lec["title"], "ready_at": ready,
            "duration": lec["transcript"]["duration"], "error": None}
    index = out / "output" / "portal" / "index.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"materials": materials}, indent=2), encoding="utf-8")
    return size + index.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="output/review.db")
    ap.add_argument("--out", default="demo")
    ap.add_argument("--keep-edits", action="store_true", help="bundle a database that has staff edits")
    ap.add_argument("--lecture", action="append", help="lecture id under output/lectures (repeatable; "
                    "default: the test lecture); --lecture none for no lectures")
    ap.add_argument("--course", default="stat-101")
    ap.add_argument("--course-title", default="Introductory Statistics")
    args = ap.parse_args()

    out = (ROOT / args.out).resolve()
    src = sqlite3.connect(args.db)
    edits = src.execute("SELECT COUNT(*) FROM edits").fetchone()[0]
    if edits and not args.keep_edits:
        raise SystemExit(f"{args.db} has {edits} staff edit(s); build a clean one with "
                         "scripts/reset_review_db.py --db ..., or pass --keep-edits")
    if out.exists():
        shutil.rmtree(out)
    db_path = out / "output" / "review.db"
    db_path.parent.mkdir(parents=True)
    con = sqlite3.connect(db_path)
    src.backup(con)  # a consistent copy, also of a database the server has open
    con.execute("PRAGMA journal_mode=DELETE")  # one file, no -wal next to it

    b = Bundle(out)
    pdfs: dict[Path, tuple[Path, set[int]]] = {}
    for doc_id, source, doc_json, regions_json in con.execute(
            "SELECT id, source_path, document_json, regions_json FROM documents").fetchall():
        if not doc_json:
            raise SystemExit(f"Document {doc_id} has no layout (still processing?)")
        doc = json.loads(doc_json)
        pdf_src = Path(source)
        pdf_dst = Path("data/samples") / pdf_src.name
        doc["source_path"] = pdf_dst.as_posix()
        for page in doc["pages"]:
            page["image_path"] = b.file(page["image_path"])
            for img in page["embedded_images"]:
                img["path"] = b.file(img["path"])
            overlay = Path("output") / doc["id"] / "overlays" / f"page_{page['number']:03d}.png"
            if (ROOT / overlay).exists():  # optional: the API answers 404 without one
                b.file(str(overlay))
        regions = [b.region(r) for r in json.loads(regions_json)]
        kept = pdfs.setdefault(pdf_src, (pdf_dst, set()))[1]
        kept.update(p["number"] for p in doc["pages"])
        con.execute("UPDATE documents SET source_path = ?, document_json = ?, regions_json = ? WHERE id = ?",
                    (pdf_dst.as_posix(), json.dumps(doc), json.dumps(regions), doc_id))
    for fid, result_json in con.execute("SELECT id, result_json FROM figures").fetchall():
        res = json.loads(result_json)
        b.region(res["region"])
        con.execute("UPDATE figures SET result_json = ? WHERE id = ?", (json.dumps(res), fid))
    con.commit()
    con.execute("VACUUM")
    con.close()

    size = b.copy()
    for pdf_src, (pdf_dst, keep) in pdfs.items():
        trimmed_pdf(pdf_src, keep, out / pdf_dst)
        size += (out / pdf_dst).stat().st_size
    size += db_path.stat().st_size
    lectures = [x for x in (args.lecture or [TEST_LECTURE]) if x != "none"]
    if lectures:
        size += bundle_lectures(lectures, args.course, args.course_title, out)
    figs = sqlite3.connect(db_path).execute("SELECT status, COUNT(*) FROM figures GROUP BY status").fetchall()
    print(f"{out}: {len(pdfs)} PDF(s), {len(b.files)} image files, {len(lectures)} lecture(s), "
          f"{size / 2**20:.1f} MB; figures {dict(figs)}")


if __name__ == "__main__":
    main()
