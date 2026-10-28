"""SAM 3.1 client (Meta Model API, Responses endpoint).

Docs: https://dev.meta.ai/docs/sam/segmenting and /docs/sam/reading-segmentation

- Concept prompt: one short noun phrase per request ("chart"). Multiple concepts → multiple calls.
- Box prompt: refine a rough box into a precise one.
- Output: streamed text lines like  <0f> 3 <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|> <|mask;...|>, ...
  We only need the boxes for figure detection, so masks are ignored.
- Zero matches = empty output, which is a valid result.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app import cache
from app.llm import _log

load_dotenv()

MODEL = os.environ.get("SAM_MODEL", "sam-3.1")
_BOX = re.compile(r"<\|box;([^|]*)\|>")


def _client() -> OpenAI:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set in .env (needed for SAM 3.1).")
    return OpenAI(base_url=os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"), api_key=key)


def parse_boxes(text: str) -> list[dict]:
    """Parse SAM output text into [{'x1','y1','x2','y2','w','h','c'}] in source-image pixels."""
    boxes = []
    for m in _BOX.finditer(text):
        fields = {}
        for part in m.group(1).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    fields[k.strip()] = float(v)
                except ValueError:
                    pass
        if {"x1", "y1", "x2", "y2"} <= fields.keys():
            boxes.append(fields)
    return boxes


def _run(image_path: str | Path, prompt: str, step: str) -> str:
    data = Path(image_path).read_bytes()
    key = cache.make_key(MODEL, step, prompt, extra=data)
    if (hit := cache.get(key)) is not None:
        _log({"step": step, "backend": "meta", "model": MODEL, "cached": True})
        return hit

    url = "data:image/png;base64," + base64.b64encode(data).decode()
    text_parts: list[str] = []
    with _client().responses.stream(
        model=MODEL,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": url},
            ],
        }],
        metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                text_parts.append(event.delta)
    text = "".join(text_parts)
    _log({"step": step, "backend": "meta", "model": MODEL, "cached": False,
          "prompt": prompt, "chars": len(text)})
    cache.put(key, text)
    return text


def segment_concept(image_path: str | Path, phrase: str) -> list[dict]:
    """Find every instance of one concept (e.g. 'chart') in the image."""
    return parse_boxes(_run(image_path, phrase, f"sam_concept:{phrase}"))


def refine_box(image_path: str | Path, x1: float, y1: float, x2: float, y2: float,
               w: int, h: int) -> dict | None:
    """Turn a rough box (e.g. from Muse Spark) into SAM's precise box. None if SAM finds nothing.

    Prompt format per docs: frame marker, object id, box token with the image's pixel size.
    """
    prompt = (f"<0f>1<|box;x1={int(x1)};y1={int(y1)};x2={int(x2)};y2={int(y2)};"
              f"w={w};h={h}|>")
    boxes = parse_boxes(_run(image_path, prompt, "sam_box_refine"))
    return boxes[0] if boxes else None
