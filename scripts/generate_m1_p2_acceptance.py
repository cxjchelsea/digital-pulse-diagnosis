#!/usr/bin/env python3
"""Generate the formal M1-P2 acceptance report (golden is read-only by default)."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "tests" / "fixtures" / "m1_sp" / "p2d_golden.json"
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p2-acceptance.json"

# Main-branch merge commits where each contract family became authoritative.
# These path trees match the respective final feature heads dd78620 and 01ca160.
M1_P0_CONTRACT_BASELINE_SHA = "4375759e0361efcf595ead656d55f42ae0ae50c6"
M1_P1_SIMULATOR_BASELINE_SHA = "c2d60a5b7e71a195207019bd413551b03c88d27a"
M1_P0_CONTRACT_PATHS = (
    "src/digital_pulse/m1_contracts.py",
    "protocols/m1-sample.schema.json",
    "protocols/m1-session.schema.json",
    "protocols/m1-quality.schema.json",
    "protocols/m1-decision.schema.json",
    "protocols/m1-report.schema.json",
)
M1_P1_SIMULATOR_SCHEMA_PATHS = (
    "protocols/m1-simulator-event.schema.json",
    "protocols/m1-simulator-expected.schema.json",
    "protocols/m1-simulator-plan.schema.json",
    "protocols/m1-simulator-scenario.schema.json",
)


@dataclass(frozen=True, slots=True)
class FrozenPathCheck:
    baseline_sha: str
    baseline_available: bool
    state: str
    returncode: int
    stderr: str

    def as_report(self) -> dict[str, object]:
        report = asdict(self)
        report["available"] = report.pop("baseline_available")
        report["sha"] = report.pop("baseline_sha")
        return report


def _check_frozen_paths(baseline_sha: str, paths: tuple[str, ...]) -> FrozenPathCheck:
    baseline = subprocess.run(
        ["git", "cat-file", "-e", f"{baseline_sha}^{{commit}}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if baseline.returncode != 0:
        return FrozenPathCheck(
            baseline_sha=baseline_sha,
            baseline_available=False,
            state="baseline_unavailable",
            returncode=baseline.returncode,
            stderr=baseline.stderr.strip(),
        )

    diff = subprocess.run(
        ["git", "diff", "--quiet", baseline_sha, "HEAD", "--", *paths],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return FrozenPathCheck(
        baseline_sha=baseline_sha,
        baseline_available=True,
        state={0: "unchanged", 1: "changed"}.get(diff.returncode, "error"),
        returncode=diff.returncode,
        stderr=diff.stderr.strip(),
    )


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


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
    p0_contracts = _check_frozen_paths(M1_P0_CONTRACT_BASELINE_SHA, M1_P0_CONTRACT_PATHS)
    p1_simulator = _check_frozen_paths(
        M1_P1_SIMULATOR_BASELINE_SHA, M1_P1_SIMULATOR_SCHEMA_PATHS
    )
    frozen_baselines = {
        "m1_p0": p0_contracts.as_report(),
        "m1_p1_simulator": p1_simulator.as_report(),
    }
    result = run_m1_p2_acceptance(
        golden_path=args.golden,
        software_commit_sha=software_commit_sha,
        source_root=ROOT / "src",
        workspace_clean=workspace_clean,
        frozen_baselines=frozen_baselines,
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
