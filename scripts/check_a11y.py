"""Run axe-core on an exported HTML file in headless Chromium and list the violations.

Usage:
  python scripts/check_a11y.py                          # newest output/*/accessible.html
  python scripts/check_a11y.py output/<doc>/accessible.html

Setup (once): pip install playwright && python -m playwright install chromium && npm install
Rules: WCAG 2.0/2.1/2.2 A and AA, plus axe best practices (heading order, landmarks, ...).
Exit code 1 if there are violations.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

AXE = ROOT / "node_modules" / "axe-core" / "axe.min.js"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        found = sorted(ROOT.glob("output/*/accessible.html"), key=lambda p: p.stat().st_mtime)
        if not found:
            sys.exit("No output/*/accessible.html yet. Run scripts/run_export.py first.")
        path = found[-1]
    if not AXE.exists():
        sys.exit("axe-core is missing. Run: npm install")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(path.resolve().as_uri())
        page.add_script_tag(path=str(AXE))
        result = page.evaluate(
            "async (tags) => await axe.run(document, {runOnly: {type: 'tag', values: tags}})", TAGS)
        version = page.evaluate("axe.version")
        browser.close()

    violations = result["violations"]
    print(f"axe-core {version} on {path}")
    print(f"{len(result['passes'])} rules passed, {len(violations)} with violations, "
          f"{len(result['incomplete'])} need a manual check")
    for v in violations:
        print(f"\n[{v['impact']}] {v['id']}: {v['help']} ({len(v['nodes'])} element(s))")
        print(f"  {v['helpUrl']}")
        for n in v["nodes"][:5]:
            print(f"  - {', '.join(map(str, n['target']))}: {n['failureSummary'].splitlines()[-1].strip()}")
    for v in result["incomplete"]:
        print(f"\n[manual check] {v['id']}: {v['help']} ({len(v['nodes'])} element(s))")
    (path.parent / "a11y_report.json").write_text(json.dumps(
        {"violations": violations, "incomplete": result["incomplete"]}, indent=2), encoding="utf-8")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
