"""Day 1 connectivity check.

Checks, for the chosen backend:
  1. Models your key can use (Meta API only; confirms auth and exact model IDs)
  2. A plain text call
  3. A vision call: the model describes a locally generated bar chart and lists its numbers

Usage:
  python scripts/test_api.py                     # backend from LLM_BACKEND in .env
  python scripts/test_api.py --backend baseten   # test Muse Spark Open on Baseten
  python scripts/test_api.py --image my_fig.png  # vision test on your own image
"""

import argparse
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.llm import chat, get_client  # noqa: E402


def make_sample_chart() -> bytes:
    """Bar chart with known values, so you can check the model's numbers."""
    img = Image.new("RGB", (480, 320), "white")
    d = ImageDraw.Draw(img)
    d.text((150, 10), "Enrollment by Year", fill="black")
    d.line((50, 280, 450, 280), fill="black", width=2)
    d.line((50, 40, 50, 280), fill="black", width=2)
    values = {"2021": 120, "2022": 160, "2023": 210, "2024": 190}
    for i, (year, v) in enumerate(values.items()):
        x0 = 80 + i * 95
        d.rectangle((x0, 280 - v, x0 + 55, 280), fill=(70, 110, 200))
        d.text((x0 + 12, 285), year, fill="black")
        d.text((x0 + 15, 265 - v), str(v), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["meta", "baseten"], default=None)
    p.add_argument("--image", default=None)
    args = p.parse_args()

    try:
        client, model, backend = get_client(args.backend)
    except Exception as e:
        sys.exit(f"Setup failed: {e}")
    print(f"Backend: {backend} | Model: {model}")

    if backend == "meta":
        print("\n[1] Models available to your key")
        try:
            for m in client.models.list().data:
                print("   -", m.id)
        except Exception as e:
            print(f"   FAILED: {e}")

    print("\n[2] Text call")
    try:
        print("   ", chat([{"role": "user", "content": "Reply with exactly: API connection works."}],
                         backend=backend, step="test_text"))
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\n[3] Vision call (expected numbers: 120, 160, 210, 190)")
    try:
        if args.image:
            data = Path(args.image).read_bytes()
            mime = "image/jpeg" if args.image.lower().endswith((".jpg", ".jpeg")) else "image/png"
        else:
            data, mime = make_sample_chart(), "image/png"
        b64 = base64.b64encode(data).decode()
        print("   ", chat([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Write alt text for this figure for a screen reader user. "
                                         "Then list every number shown in the figure."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }], backend=backend, step="test_vision"))
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\nCalls logged to output/llm_calls.jsonl")


if __name__ == "__main__":
    main()
