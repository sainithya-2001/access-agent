"""One chat client for both backends, so every agent step can run on either.

Usage:
    from app.llm import get_client
    client, model, backend = get_client()            # uses LLM_BACKEND from .env
    client, model, backend = get_client("baseten")   # force a backend

Every call made through `chat()` is logged with its backend, which is the
evidence for the open-weights prize that each step really ran on Muse Spark Open.
"""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app import cache

load_dotenv()

BACKENDS = {
    "meta": ("MODEL_API_KEY", "MODEL_API_BASE", "SPARK_MODEL",
             "https://api.meta.ai/v1", "muse-spark-1.1"),
    "baseten": ("BASETEN_API_KEY", "BASETEN_API_BASE", "BASETEN_MODEL",
                "https://inference.baseten.co/v1", "meta-models/muse-spark-open-1026"),
}

LOG_PATH = Path("output/llm_calls.jsonl")
_THREAD_LOCK = threading.Lock()  # the file lock below is per process; threads of one process need this too
# Set for repeated benchmark runs ("rep1", "rep2", ...): a cache namespace per repeat, so each repeat
# makes its own model calls (and is itself cached). Empty for normal runs.
RUN_TAG = ""

# DEMO_MODE (app/api.py) sets BLOCKED: then a model call (a cache miss, here or in app/sam.py) raises,
# except while a passcode upload or recrop runs inside `allow_calls()`. A count, not a per-thread
# flag, because a run's figures go to a thread pool.
BLOCKED = False
_allowed = 0
_allowed_lock = threading.Lock()


class ModelCallsBlocked(RuntimeError):
    pass


@contextmanager
def allow_calls():
    global _allowed
    with _allowed_lock:
        _allowed += 1
    try:
        yield
    finally:
        with _allowed_lock:
            _allowed -= 1


def check_allowed(step: str) -> None:
    if BLOCKED and not _allowed:
        raise ModelCallsBlocked(f"Model calls are off in demo mode (step '{step}')")


def get_client(backend: str | None = None):
    backend = (backend or os.environ.get("LLM_BACKEND", "meta")).lower()
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend '{backend}'. Use one of: {list(BACKENDS)}")
    key_var, base_var, model_var, default_base, default_model = BACKENDS[backend]
    api_key = os.environ.get(key_var)
    if not api_key:
        raise RuntimeError(f"{key_var} is not set in .env (needed for backend '{backend}').")
    client = OpenAI(base_url=os.environ.get(base_var, default_base), api_key=api_key)
    return client, os.environ.get(model_var, default_model), backend


def chat(messages, backend: str | None = None, step: str = "", use_cache: bool = True,
         model: str | None = None, **kwargs) -> str:
    """Send a chat request (cached), and log which backend/model handled it.

    Usage is logged per call to output/llm_calls.jsonl so spend can be tracked.
    model: use another model on the same backend (e.g. the benchmark judge, muse-wonder-1.0).
    """
    client, default_model, backend = get_client(backend)
    model = model or default_model
    key = cache.make_key(model, step, [messages, kwargs] + ([RUN_TAG] if RUN_TAG else []))
    if use_cache and (hit := cache.get(key)) is not None:
        _log({"step": step, "backend": backend, "model": model, "cached": True, "key": key,
              "original": cache.usage(key)})  # tokens of the call that filled the cache, if known
        return hit

    check_allowed(step)
    start = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    spent = {"input_tokens": getattr(usage, "prompt_tokens", None),
             "output_tokens": getattr(usage, "completion_tokens", None),
             "seconds": round(time.time() - start, 2)}
    _log({
        "step": step,
        "backend": backend,
        "model": model,
        "cached": False,
        "key": key,  # lets later cached hits be traced to this call's tokens (full-cost estimates)
        "request_id": getattr(resp, "id", None),
        **spent,
    })
    if use_cache:
        cache.put(key, text, spent)
    return text


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # pid and run tag: runs in parallel share this log, so each run counts only its own lines.
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid(),
             **({"run_tag": RUN_TAG} if RUN_TAG else {}), **entry}
    line = (json.dumps(entry) + "\n").encode("utf-8")
    # Parallel runs append to the same file; without a lock their writes can interleave mid-line.
    with _THREAD_LOCK, open(LOG_PATH.with_suffix(".lock"), "a+b") as lock:
        _lock(lock)
        try:
            with LOG_PATH.open("ab") as f:
                f.write(line)
        finally:
            _unlock(lock)


if os.name == "nt":
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for ~10 s, then raises

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)
