#!/usr/bin/env python3
"""Run M1-P1 formal acceptance in a temporary directory and print JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "tests" / "fixtures" / "m1_simulator" / "golden_summaries.json"
D3_ACCEPTANCE = ROOT / "artifacts" / "d3-acceptance" / "acceptance.json"


def _load_d3_result() -> bool | None:
    if not D3_ACCEPTANCE.is_file():
        return None
    try:
        payload = json.loads(D3_ACCEPTANCE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("formal_acceptance")
    return value if isinstance(value, bool) else None


def _run_d3_acceptance() -> bool:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_d3_acceptance.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    result = _load_d3_result()
    return bool(result)


def resolve_d3_gate(*, skip: bool) -> tuple[bool | None, bool]:
    """Return (d3_regression_passed, d3_regression_skipped).

    Default: read existing D3 acceptance artifact, otherwise run D3.
    ``--skip-d3-gate``: do not claim pass; mark skipped and exclude from overall acceptance.
    """
    if skip:
        return None, True
    existing = _load_d3_result()
    if existing is not None:
        return existing, False
    return _run_d3_acceptance(), False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P1 formal acceptance")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--skip-d3-gate",
        action="store_true",
        help="Skip D3 verification; sets d3_regression_skipped=true and does not claim d3_regression_passed",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_simulator.acceptance import run_m1_p1_acceptance

    d3_passed, d3_skipped = resolve_d3_gate(skip=bool(args.skip_d3_gate))
    result = run_m1_p1_acceptance(
        golden_path=args.golden,
        d3_regression_passed=d3_passed,
        d3_regression_skipped=d3_skipped,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.acceptance else 1


if __name__ == "__main__":
    raise SystemExit(main())
