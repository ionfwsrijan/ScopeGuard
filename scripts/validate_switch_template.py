"""Validate the Scope Guard Switch template and print a reproducible report.

Usage:
    python scripts/validate_switch_template.py
    python scripts/validate_switch_template.py --json

The structural validator always runs. Switch's own advisory linter runs too
when ``.reference-switch/core`` is present, and is reported as skipped
otherwise. Exit status is non-zero only when a structural check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scopeguard.template import upstream_lint, validate_file  # noqa: E402

TEMPLATE = ROOT / "templates" / "scopeguard.template.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    text = args.template.read_text(encoding="utf-8")
    report = validate_file(args.template)
    report.upstream = upstream_lint(text, ROOT / ".reference-switch" / "core")

    if args.json:
        print(
            json.dumps(
                {
                    "template": str(args.template),
                    "ok": report.ok,
                    "findings": [
                        {"code": finding.code, "message": finding.message}
                        for finding in report.findings
                    ],
                    "upstream": report.upstream,
                },
                indent=2,
            )
        )
    else:
        print(f"template: {args.template}")
        print(f"structural: {'passed' if report.ok else 'failed'}")
        for finding in report.findings:
            print(f"  {finding.code}: {finding.message}")
        print(f"upstream switch lint: {report.upstream}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
