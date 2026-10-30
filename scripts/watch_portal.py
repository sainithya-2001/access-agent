"""Watch the course portal folder, data/portal/<course>/, and run the lecture pipeline on each new video
(app/portal.py). The API server does the same with PORTAL_WATCH=1.

  python scripts/watch_portal.py            # poll every 10 s until Ctrl+C
  python scripts/watch_portal.py --once     # one pass: process every settled video not yet done
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app import portal  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()
    portal.PORTAL.mkdir(parents=True, exist_ok=True)
    if args.once:
        done = portal.scan(echo=True)
        print(f"{len(done)} video(s) processed")
        return
    print(f"Watching {portal.PORTAL.resolve()} every {args.interval:g} s (Ctrl+C to stop)")
    try:
        portal.watch(args.interval, echo=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
