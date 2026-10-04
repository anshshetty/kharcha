#!/usr/bin/env python3
"""Create a clearly marked fictional PDF using a disposable demo ledger."""

import argparse
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.pdf_report import build_pdf, parse_options  # noqa: E402
from scripts.demo import demo_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 15))
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--include-transactions", action="store_true")
    args = parser.parse_args()
    options = parse_options(
        {
            "start": args.start,
            "end": args.as_of.isoformat(),
            "include_transactions": args.include_transactions,
        }
    )
    with demo_store(args.as_of) as store:
        content = build_pdf(store, options)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwrites, including symlinks, instead of replacing an existing report.
    with args.output.open("xb") as output:
        output.write(content)
    print("Created synthetic demo PDF:", args.output)


if __name__ == "__main__":
    main()
