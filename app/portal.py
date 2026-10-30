"""Course portal (lecture slice, step 7): a watched folder per course; a new lecture video there runs
the lecture pipeline, and the student page lists what is ready.

  data/portal/<course>/<video>.mp4        drop a recording here (course = folder name)
  data/portal/<course>/course.json        optional: {"title": "Introductory Statistics"}
  python scripts/watch_portal.py [--once]  or PORTAL_WATCH=1 with the API server (app/api.py)
  PORTAL_DIR, PORTAL_INDEX                 other locations for the folder and the index

A video is processed once it has stopped growing (same size and mtime on two polls, so a file still
being copied is left alone), then recorded in output/portal/index.json with its course, file, lecture
id, title, status (ready / failed + error) and when it was ready. A changed file (new size or mtime)
is processed again. process_lecture is cached, so re-processing an unchanged recording costs $0.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.cache import write_atomic

PORTAL = Path(os.environ.get("PORTAL_DIR", "data/portal"))
INDEX = Path(os.environ.get("PORTAL_INDEX", "output/portal/index.json"))
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
_lock = threading.Lock()  # one scan at a time (the API thread and a manual scan)


def course_title(folder: Path) -> str:
    try:
        return json.loads((folder / "course.json").read_text(encoding="utf-8"))["title"]
    except (OSError, ValueError, KeyError):
        return folder.name.replace("-", " ").replace("_", " ").title()


def load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"materials": {}}


def _save(index: dict) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(INDEX, json.dumps(index, indent=2))


def _stamp(path: Path) -> list:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def scan(process=None, echo: bool = False, seen: dict | None = None) -> list[str]:
    """One pass over data/portal. seen: {file: stamp} from the previous pass (a file is processed only
    when its stamp matches the last pass, i.e. it stopped growing); None = process settled files now.
    process: the function to run on a new video (default app.lecture.process_lecture).
    Returns the keys processed in this pass."""
    if process is None:
        from app.lecture import process_lecture as process
    done = []
    with _lock:
        index = load_index()
        for folder in sorted(p for p in PORTAL.glob("*") if p.is_dir()):
            for video in sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT):
                key = f"{folder.name}/{video.name}"
                stamp = _stamp(video)
                if seen is not None:
                    previous, seen[key] = seen.get(key), stamp
                    if previous != stamp:
                        continue  # new or still growing: wait one more poll
                entry = index["materials"].get(key)
                if entry and entry.get("stamp") == stamp and entry.get("status") in ("ready", "failed"):
                    continue
                if echo:
                    print(f"Processing {key}")
                entry = {"course": folder.name, "course_title": course_title(folder), "file": video.name,
                         "stamp": stamp, "status": "processing", "added": entry.get("added") if entry else _now()}
                index["materials"][key] = entry
                _save(index)
                try:
                    lec = process(video, title=None)
                    entry.update(status="ready", lecture_id=lec.id, title=lec.title, ready_at=_now(),
                                 duration=lec.transcript.duration, error=None)
                except Exception as e:  # recorded; the watcher keeps running
                    entry.update(status="failed", error=f"{e.__class__.__name__}: {e}", ready_at=None)
                index["materials"][key] = entry
                _save(index)
                done.append(key)
                if echo:
                    print(f"  {entry['status']}: {entry.get('title') or entry.get('error')}")
    return done


def watch(interval: float = 10.0, stop: threading.Event | None = None, echo: bool = False) -> None:
    seen: dict = {}
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            scan(seen=seen, echo=echo)
        except Exception as e:  # a bad file must not stop the watcher
            if echo:
                print(f"Portal scan failed: {e}")
        stop.wait(interval)


def courses() -> list[dict]:
    """For the student page: every course folder with its ready / processing / failed materials, newest
    first. Lecture pages are served from /lectures/<lecture id>/notes.html (app/api.py)."""
    index = load_index()
    out = []
    folders = sorted(p for p in PORTAL.glob("*") if p.is_dir()) if PORTAL.exists() else []
    names = {f.name: course_title(f) for f in folders}
    for m in index["materials"].values():
        names.setdefault(m["course"], m.get("course_title") or m["course"])
    for name, title in sorted(names.items(), key=lambda kv: kv[1].lower()):
        items = [m for m in index["materials"].values() if m["course"] == name]
        items.sort(key=lambda m: m.get("ready_at") or m.get("added") or "", reverse=True)
        out.append({"id": name, "title": title, "materials": [{
            "file": m["file"], "title": m.get("title") or Path(m["file"]).stem, "status": m["status"],
            "ready_at": m.get("ready_at"), "duration": m.get("duration"),
            "url": f"/lectures/{m['lecture_id']}/notes.html" if m["status"] == "ready" else None} for m in items]})
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
