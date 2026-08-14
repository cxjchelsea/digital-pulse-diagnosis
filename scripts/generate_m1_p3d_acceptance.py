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


def _strip_ansi(text: str) -> str:
    """移除 Vitest/CI 彩色输出中的 ANSI 转义，便于稳定解析。"""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _parse_vitest_counts(output: str) -> tuple[int | None, int | None]:
    """解析 Vitest 摘要。

    优先读取 ``Tests N passed``（用例数），否则回退 ``Test Files N passed``。
    返回 (passed_count, failed_count)；不根据计数判定成功——returncode 仍是权威。
    """
    cleaned = _strip_ansi(output)
    tests_passed = re.search(r"(?:^|\n)\s*Tests\s+.*?(\d+)\s+passed", cleaned)
    files_passed = re.search(r"Test Files\s+(\d+)\s+passed", cleaned)
    tests_failed = re.search(r"(?:^|\n)\s*Tests\s+(\d+)\s+failed", cleaned)
    passed_count = (
        int(tests_passed.group(1))
        if tests_passed
        else (int(files_passed.group(1)) if files_passed else None)
    )
    failed_count = int(tests_failed.group(1)) if tests_failed else None
    return passed_count, failed_count


def _web_env(software_commit_sha: str | None = None) -> dict[str, str]:
    """向 Vite/Vitest 注入真实软件提交，禁止前端回落到全零哨兵。"""
    env = dict(os.environ)
    sha = (software_commit_sha or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", sha) and sha != "0" * 40:
        env["VITE_M1_SOFTWARE_COMMIT_SHA"] = sha
        env["GITHUB_SHA"] = sha
    return env


def _run_web_tests(*, software_commit_sha: str) -> tuple[bool, dict[str, object]]:
    proc = subprocess.run(
        _npm_command("test", "--", "--run"),
        cwd=ROOT / "web",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_web_env(software_commit_sha),
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed_count, failed_count = _parse_vitest_counts(output)
    summary = {
        "returncode": proc.returncode,
        "passed": passed_count,
        "failed": failed_count,
        "tail": "\n".join(output.strip().splitlines()[-40:]),
    }
    # 进程退出码权威：解析到的 passed 不得覆盖失败退出。
    return proc.returncode == 0, summary


def _run_web_build(*, software_commit_sha: str) -> bool:
    install = subprocess.run(
        _npm_command("ci"),
        cwd=ROOT / "web",
        check=False,
        env=_web_env(software_commit_sha),
    )
    if install.returncode != 0:
        return False
    build = subprocess.run(
        _npm_command("run", "build"),
        cwd=ROOT / "web",
        check=False,
        env=_web_env(software_commit_sha),
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
        web_build_passed = _run_web_build(software_commit_sha=software_commit_sha)
    web_tests_passed, web_test_summary = _run_web_tests(software_commit_sha=software_commit_sha)

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
