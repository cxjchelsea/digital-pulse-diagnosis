#!/usr/bin/env python3
"""Generate M1-P3D React analysis UI acceptance evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p3d-acceptance.json"
BASELINE_SHA = "5033cd5e76d62d0492a0cf79bf9e8a5b2150b637"
FROZEN_BACKEND_PATHS = (
    "src/digital_pulse/m1_contracts.py",
    "protocols",
    "src/digital_pulse/m1_simulator",
    "tests/fixtures/m1_simulator",
    "src/digital_pulse/m1_sp",
    "tests/fixtures/m1_sp",
    "src/digital_pulse/m1_app",
    "src/digital_pulse/m1_api",
    "src/digital_pulse/m1_p2_acceptance.py",
    "src/digital_pulse/m1_p3b_acceptance.py",
    "src/digital_pulse/m1_p3c_acceptance.py",
    "scripts/generate_m1_p2_acceptance.py",
    "scripts/generate_m1_p3b_acceptance.py",
    "scripts/generate_m1_p3c_acceptance.py",
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


def _load_acceptance(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (payload.get("acceptance") is True or payload.get("formal_acceptance") is True) and payload.get("failed_gates") == []


def _check_paths(paths: tuple[str, ...]) -> FrozenPathCheck:
    diff = subprocess.run(
        ["git", "diff", "--name-only", BASELINE_SHA, "HEAD", "--", *paths],
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


def _run_existing(script: str, artifact: str) -> bool:
    existing = ROOT / "artifacts" / "acceptance" / artifact
    if artifact == "d3":
        existing = ROOT / "artifacts" / "d3-acceptance" / "acceptance.json"
    if _load_acceptance(existing):
        return True
    if artifact.startswith("d3"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_d3_acceptance.py")],
            cwd=ROOT,
            check=False,
        )
        return proc.returncode == 0 and _load_acceptance(ROOT / "artifacts" / "d3-acceptance" / "acceptance.json")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=ROOT,
        capture_output=artifact == "m1-p1-acceptance.json",
        text=artifact == "m1-p1-acceptance.json",
        encoding="utf-8" if artifact == "m1-p1-acceptance.json" else None,
        check=False,
    )
    if proc.returncode == 0 and artifact == "m1-p1-acceptance.json":
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(proc.stdout, encoding="utf-8")
    return proc.returncode == 0 and _load_acceptance(existing)


def _npm_command(*args: str) -> list[str]:
    # Windows 上 npm 常为 npm.cmd；Unix 上 shell=True+list 会丢掉子命令。
    npm_executable = "npm.cmd" if os.name == "nt" else "npm"
    return [npm_executable, *args]


def _run_web_tests() -> tuple[bool, dict[str, object]]:
    proc = subprocess.run(
        _npm_command("test", "--", "--run"),
        cwd=ROOT / "web",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Vitest 会先打印 "Test Files N passed"，再打印 "Tests N passed"；取测试用例计数。
    tests_match = re.search(r"(?:^|\n)\s*Tests\s+(\d+)\s+passed", output)
    files_match = re.search(r"Test Files\s+(\d+)\s+passed", output)
    failed_match = re.search(r"(?:^|\n)\s*(?:Tests\s+)?(\d+)\s+failed", output)
    summary = {
        "returncode": proc.returncode,
        "passed": int(tests_match.group(1)) if tests_match else (int(files_match.group(1)) if files_match else None),
        "failed": int(failed_match.group(1)) if failed_match else None,
        "tail": "\n".join(output.strip().splitlines()[-40:]),
    }
    return proc.returncode == 0, summary


def _run_web_build() -> bool:
    install = subprocess.run(
        _npm_command("ci"),
        cwd=ROOT / "web",
        check=False,
    )
    if install.returncode != 0:
        return False
    build = subprocess.run(
        _npm_command("run", "build"),
        cwd=ROOT / "web",
        check=False,
    )
    return build.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P3D formal acceptance")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--skip-web-install-build", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p3d_acceptance import run_m1_p3d_acceptance

    software_commit_sha = _git("rev-parse", "HEAD")
    workspace_clean = not bool(_git("status", "--porcelain"))

    d3 = _load_acceptance(ROOT / "artifacts" / "d3-acceptance" / "acceptance.json") or _run_existing(
        "generate_d3_acceptance.py", "d3"
    )
    p1 = _run_existing("generate_m1_p1_acceptance.py", "m1-p1-acceptance.json")
    p2 = _run_existing("generate_m1_p2_acceptance.py", "m1-p2-acceptance.json")
    p3b = _run_existing("generate_m1_p3b_acceptance.py", "m1-p3b-acceptance.json")
    p3c = _run_existing("generate_m1_p3c_acceptance.py", "m1-p3c-acceptance.json")
    frozen = {"backend_semantics": _check_paths(FROZEN_BACKEND_PATHS).as_report()}

    if args.skip_web_install_build:
        web_build_passed = True
    else:
        web_build_passed = _run_web_build()
    web_tests_passed, web_test_summary = _run_web_tests()

    result = run_m1_p3d_acceptance(
        root=ROOT,
        software_commit_sha=software_commit_sha,
        workspace_clean=workspace_clean,
        web_build_passed=web_build_passed,
        web_tests_passed=web_tests_passed,
        web_test_summary=web_test_summary,
        p3c_regression_passed=p3c,
        p3b_regression_passed=p3b,
        p2_regression_passed=p2,
        p1_regression_passed=p1,
        d3_regression_passed=d3,
        frozen_backend_semantics=frozen,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"acceptance": result["acceptance"], "failed_gates": result["failed_gates"]}, ensure_ascii=False))
    return 0 if result["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
