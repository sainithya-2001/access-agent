"""Check benchmark items by hand: shows each unverified item's image and draft, then asks.

  python eval/check_benchmark.py

For every item in eval/benchmark.json with "verified": false:
  - opens the figure's crop (output/*/crops/<region_id>.png); if the page was never detected,
    the whole PDF page is rendered instead (free, no model calls)
  - prints caption, type, axis titles, true values, expected outcome, notes, reference description
  - asks: y = correct (sets "verified": true and saves at once), e = edit the item in Notepad,
    s = skip, q = quit
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pymupdf as fitz

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "eval" / "benchmark.json"
PREVIEWS = ROOT / "output" / "benchmark_check"


def load() -> dict:
    return json.loads(BENCH.read_text(encoding="utf-8"))


def save(bench: dict) -> None:
    """Write to a temp file first, so an interrupted save never leaves a broken benchmark."""
    tmp = BENCH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bench, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, BENCH)


def image_for(item: dict, pdf_path: str) -> Path:
    crops = sorted(glob.glob(str(ROOT / "output" / "*" / "crops" / f"{item['region_id']}.png")))
    if crops:
        return Path(crops[0])
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    out = PREVIEWS / f"page_{item['page']:03d}.png"
    if not out.exists():
        fitz.open(ROOT / pdf_path)[item["page"] - 1].get_pixmap(dpi=110).save(out)
    return out


def open_file(path: Path) -> None:
    try:
        os.startfile(path)  # Windows: default image viewer
    except (AttributeError, OSError):
        import webbrowser
        webbrowser.open(path.as_uri())


def wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def values_text(data) -> str:
    if not data:
        return "    (none: the book gives no values)"
    lines = []
    for s in data.get("series", []):
        pts = ", ".join(f"{p['label']}: {p['value'] if p['value'] is not None else '?'}" for p in s["points"])
        lines.append(wrap(f"{s.get('name') or 'values'}: {pts}"))
    if "stems" in data:
        lines.append(wrap("stems: " + "; ".join(f"{k} | {v}" for k, v in data["stems"].items())))
    if "values" in data:
        lines.append(wrap(f"all values ({len(data['values'])}): {', '.join(map(str, data['values']))}"))
    for k, v in data.items():
        if k not in ("series", "stems", "values"):
            lines.append(wrap(f"{k}: {json.dumps(v, ensure_ascii=False)}"))
    return "\n".join(lines)


def show(item: dict, n: int, total: int, image: Path) -> None:
    ref = item.get("reference") or {}
    print("\n" + "=" * 100)
    print(f"{n}/{total}  {item['id']}  (PDF page {item['page']}, region {item['region_id']})   image: {image.name}")
    print("=" * 100)
    print(f"Caption:   {item.get('caption')}")
    print(f"Type:      {item.get('type')}  (region type: {item.get('region_type')})")
    print(f"Title:     {item.get('title')}")
    print(f"x-axis:    {item.get('x_axis')}")
    print(f"y-axis:    {item.get('y_axis')}")
    print(f"True values (source: {item.get('values_source')}):")
    print(values_text(item.get("data")))
    print(f"Expected:  {item.get('expected_outcome')}")
    if item.get("notes"):
        print("Notes:\n" + wrap(item["notes"]))
    print("Reference alt text:\n" + wrap(ref.get("alt_text") or "(none)"))
    print("Reference long description:\n" + wrap(ref.get("long_description") or "(none)"))


def edit(item: dict) -> dict:
    """Open the item in Notepad (blocks until closed); reopen until the JSON is valid."""
    with tempfile.NamedTemporaryFile("w", suffix=f"_{item['id']}.json", delete=False, encoding="utf-8") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)
        path = f.name
    try:
        while True:
            subprocess.run(["notepad.exe", path])
            try:
                new = json.loads(Path(path).read_text(encoding="utf-8-sig"))
                if not isinstance(new, dict) or new.get("id") != item["id"]:
                    raise ValueError(f"the item must stay a JSON object with \"id\": \"{item['id']}\"")
                return new
            except ValueError as e:
                print(f"  Not saved: {e}")
                if input("  Reopen in Notepad? [Y/n] ").strip().lower() == "n":
                    return item
    finally:
        os.unlink(path)


def main() -> None:
    bench = load()
    todo = [f["id"] for f in bench["figures"] if not f.get("verified")]
    if not todo:
        print("Every item is verified.")
        return
    print(f"{len(todo)} unverified of {len(bench['figures'])} items. y = correct, e = edit in Notepad, s = skip, q = quit")
    done = 0
    for n, fid in enumerate(todo, 1):
        opened = False
        while True:
            bench = load()  # pick up any edits made elsewhere since the last save
            idx = next(i for i, f in enumerate(bench["figures"]) if f["id"] == fid)
            item = bench["figures"][idx]
            image = image_for(item, bench["source_pdf"])
            show(item, n, len(todo), image)
            if not opened:  # once per item, not again after an edit or a typo
                open_file(image)
                opened = True
            ans = input(f"[{n}/{len(todo)}] Correct? (y / e / s / q) ").strip().lower()
            if ans == "y":
                item["verified"] = True
                save(bench)
                done += 1
                print(f"  Saved: {fid} verified.")
                break
            if ans == "e":
                bench["figures"][idx] = edit(item)
                save(bench)
                print("  Edit saved. Showing it again.")
                continue
            if ans == "s":
                break
            if ans == "q":
                print(f"\nVerified {done} this session.")
                return
            print("  Please type y, e, s, or q.")
    left = sum(1 for f in load()["figures"] if not f.get("verified"))
    print(f"\nVerified {done} this session; {left} still unverified.")


if __name__ == "__main__":
    main()
