#!/usr/bin/env python3
"""Generate M1-P4B-C event-persistence slice acceptance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p4b-c-acceptance.json"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P4B-C slice acceptance")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p4b_c_acceptance import ACCEPTANCE_VERSION, run_m1_p4b_c_acceptance

    software_commit_sha = _git("rev-parse", "HEAD")
    expected_head = os.environ.get("EXPECTED_HEAD_SHA") or software_commit_sha
    if expected_head != software_commit_sha:
        print(f"exact-head mismatch expected={expected_head} actual={software_commit_sha}", file=sys.stderr)
        return 1
    result = run_m1_p4b_c_acceptance(
        software_commit_sha=software_commit_sha,
        expected_head_sha=expected_head,
    )
    if result.get("acceptance_version") != ACCEPTANCE_VERSION:
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
