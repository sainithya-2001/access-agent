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
import time
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
         **kwargs) -> str:
    """Send a chat request (cached), and log which backend/model handled it.

    Usage is logged per call to output/llm_calls.jsonl so spend can be tracked.
    """
    client, model, backend = get_client(backend)
    key = cache.make_key(model, step, [messages, kwargs])
    if use_cache and (hit := cache.get(key)) is not None:
        _log({"step": step, "backend": backend, "model": model, "cached": True})
        return hit

    start = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    _log({
        "step": step,
        "backend": backend,
        "model": model,
        "cached": False,
        "request_id": getattr(resp, "id", None),
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "seconds": round(time.time() - start, 2),
    })
    if use_cache:
        cache.put(key, text)
    return text


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
