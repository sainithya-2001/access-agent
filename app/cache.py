"""Disk cache for model responses, so re-running the same page costs nothing.

Key = hash of (model, step name, prompt messages, image bytes). Images are hashed
by content, so the same crop always hits the cache even under a new file name.
"""

import hashlib
import json
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


def get(key: str) -> str | None:
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())["response"]
    return None


def put(key: str, response: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps({"response": response}))
