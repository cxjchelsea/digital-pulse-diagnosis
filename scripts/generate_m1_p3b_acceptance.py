#!/usr/bin/env python3
"""Generate M1-P3B replay/projection/gate acceptance evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p3b-acceptance.json"
P3A_BASELINE_SHA = "d3692bfc7753fe5e61fe1c319dd31fd4280bfcf4"
P0_PATHS = (
    "src/digital_pulse/m1_contracts.py",
    "protocols/m1-sample.schema.json",
    "protocols/m1-session.schema.json",
    "protocols/m1-quality.schema.json",
    "protocols/m1-decision.schema.json",
    "protocols/m1-report.schema.json",
)
P1_PATHS = (
    "src/digital_pulse/m1_simulator",
    "tests/fixtures/m1_simulator",
)
P2_PATHS = (
    "src/digital_pulse/m1_sp",
    "tests/fixtures/m1_sp",
    "tests/test_m1_sp_beats.py",
    "tests/test_m1_sp_integrity.py",
    "tests/test_m1_sp_normalization.py",
    "tests/test_m1_sp_p2d_acceptance.py",
    "tests/test_m1_sp_p2d_replay.py",
    "tests/test_m1_sp_summary.py",
    "scripts/generate_m1_p2_acceptance.py",
    "src/digital_pulse/m1_p2_acceptance.py",
)


@dataclass(frozen=True, slots=True)
class FrozenPathCheck:
    state: str
    path_count: int
    changed_paths: tuple[str, ...]

    def as_report(self) -> dict[str, object]:
        return asdict(self)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def _check_paths(paths: tuple[str, ...]) -> FrozenPathCheck:
    diff = subprocess.run(
        ["git", "diff", "--name-only", P3A_BASELINE_SHA, "HEAD", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    changed = tuple(line for line in diff.stdout.splitlines() if line.strip())
    return FrozenPathCheck(
        state="unchanged" if diff.returncode == 0 and not changed else "changed",
        path_count=len(paths),
        changed_paths=changed,
    )


def _load_acceptance(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("acceptance") is True or payload.get("formal_acceptance") is True
    ) and payload.get("failed_gates") == []


def _ensure_p1() -> bool:
    existing = ROOT / "artifacts" / "acceptance" / "m1-p1-acceptance.json"
    if _load_acceptance(existing):
        return True
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_m1_p1_acceptance.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    else:
        target = ROOT / "artifacts" / "acceptance" / "m1-p1-acceptance.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proc.stdout, encoding="utf-8")
    return proc.returncode == 0


def _ensure_p2() -> bool:
    existing = ROOT / "artifacts" / "acceptance" / "m1-p2-acceptance.json"
    if _load_acceptance(existing):
        return True
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_m1_p2_acceptance.py")],
        cwd=ROOT,
        check=False,
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P3B formal acceptance")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p3b_acceptance import run_m1_p3b_acceptance

    software_commit_sha = _git("rev-parse", "HEAD")
    workspace_clean = not bool(_git("status", "--porcelain"))
    d3 = _load_acceptance(ROOT / "artifacts" / "d3-acceptance" / "acceptance.json")
    p1 = _ensure_p1()
    p2 = _ensure_p2()
    frozen = {
        "p0": _check_paths(P0_PATHS).as_report(),
        "p1": _check_paths(P1_PATHS).as_report(),
        "p2": _check_paths(P2_PATHS).as_report(),
    }
    result = run_m1_p3b_acceptance(
        software_commit_sha=software_commit_sha,
        workspace_clean=workspace_clean,
        frozen_baselines=frozen,
        d3_regression_passed=d3,
        m1_p1_regression_passed=p1,
        m1_p2_regression_passed=p2,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True))
    return 0 if result["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
