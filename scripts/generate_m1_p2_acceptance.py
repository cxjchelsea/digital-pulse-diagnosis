#!/usr/bin/env python3
"""Generate the formal M1-P2 acceptance report (golden is read-only by default)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "tests" / "fixtures" / "m1_sp" / "p2d_golden.json"
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p2-acceptance.json"


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def _git_success(*args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True
    ).returncode == 0


def _d3_regression_passed() -> bool:
    path = ROOT / "artifacts" / "d3-acceptance" / "acceptance.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("formal_acceptance") is True and payload.get("failed_gates") == []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P2 formal acceptance")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--write-golden",
        action="store_true",
        help="Explicitly replace the selected golden file with the current canonical summary",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p2_acceptance import run_m1_p2_acceptance

    software_commit_sha = _git("rev-parse", "HEAD")
    workspace_clean = not bool(_git("status", "--porcelain"))
    d3_passed = _d3_regression_passed()
    from digital_pulse.m1_simulator.acceptance import run_m1_p1_acceptance

    p1 = run_m1_p1_acceptance(
        golden_path=ROOT / "tests" / "fixtures" / "m1_simulator" / "golden_summaries.json",
        d3_regression_passed=d3_passed,
    )
    contracts_unchanged = _git_success(
        "diff",
        "--quiet",
        "849dda5fa6ef2ae165f982d8b565cdfaa8a3a643",
        "--",
        "src/digital_pulse/m1_contracts.py",
        "protocols/m1-session.schema.json",
        "protocols/m1-sample.schema.json",
        "protocols/m1-quality.schema.json",
        "protocols/m1-decision.schema.json",
        "protocols/m1-report.schema.json",
        "protocols/m1-simulator-event.schema.json",
        "protocols/m1-simulator-expected.schema.json",
        "protocols/m1-simulator-plan.schema.json",
        "protocols/m1-simulator-scenario.schema.json",
    )
    result = run_m1_p2_acceptance(
        golden_path=args.golden,
        software_commit_sha=software_commit_sha,
        source_root=ROOT / "src",
        workspace_clean=workspace_clean,
        m1_contracts_unchanged=contracts_unchanged,
        d3_regression_passed=d3_passed,
        m1_p1_regression_passed=(p1.acceptance and not p1.failed_gates),
        write_golden=bool(args.write_golden),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(
        (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True))
    # Golden authoring is intentionally distinct from clean-tree formal acceptance.
    if args.write_golden:
        return 0
    return 0 if result["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
