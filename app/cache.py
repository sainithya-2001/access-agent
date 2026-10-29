"""Disk cache for model responses, so re-running the same page costs nothing.

Key = hash of (model, step name, prompt messages, image bytes). Images are hashed
by content, so the same crop always hits the cache even under a new file name.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

CACHE_DIR = Path("cache/llm")


def make_key(model: str, step: str, messages, extra: bytes | None = None) -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(step.encode())
    h.update(json.dumps(messages, sort_keys=True, default=str).encode())
    if extra:
        h.update(extra)
    return h.hexdigest()


def _entry(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None  # missing, or (from before writes were atomic) half-written: a miss


def get(key: str) -> str | None:
    e = _entry(key)
    return e["response"] if e else None


def usage(key: str) -> dict | None:
    """Tokens and seconds of the original (uncached) call, if recorded (entries since Oct 28)."""
    e = _entry(key)
    return e.get("usage") if e else None


def put(key: str, response: str, usage: dict | None = None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"response": response} | ({"usage": usage} if usage else {})
    write_atomic(CACHE_DIR / f"{key}.json", json.dumps(entry))


def write_atomic(path: Path, text: str) -> None:
    """Threads and parallel runs may write the same key: write a temp file, then rename it into
    place, so a reader never sees half an entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except PermissionError:  # Windows: another writer has the file open; its entry is just as good
        tmp.unlink(missing_ok=True)
