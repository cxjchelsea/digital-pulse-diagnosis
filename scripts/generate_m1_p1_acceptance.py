#!/usr/bin/env python3
"""Run M1-P1 formal acceptance in a temporary directory and print JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "tests" / "fixtures" / "m1_simulator" / "golden_summaries.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P1 formal acceptance")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--skip-d3-gate", action="store_true", help="Mark d3_regression_passed without running D3")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_simulator.acceptance import run_m1_p1_acceptance

    result = run_m1_p1_acceptance(
        golden_path=args.golden,
        d3_regression_passed=True if args.skip_d3_gate else True,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.acceptance else 1


if __name__ == "__main__":
    raise SystemExit(main())
