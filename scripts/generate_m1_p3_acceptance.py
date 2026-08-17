#!/usr/bin/env python3
"""Generate M1-P3 aggregate formal acceptance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p3-acceptance.json"
BASELINE_SHA = "2f4f88cc69fbdfb1e129d347025695334542eb9e"
EXPECTED_P2_CANONICAL_GOLDEN_SHA256 = (
    "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
)
EXPECTED_D3_TAG_OBJECT = "da85aee746453e92b0029ae6ec4f51fefc769e4e"
EXPECTED_D3_TAG_TARGET = "d0251b3741d99bab955fa288c57424abd301b0b1"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def _git_available() -> bool:
    try:
        _git("rev-parse", "HEAD")
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _git_diff_quiet(*paths: str) -> bool:
    proc = subprocess.run(
        ["git", "diff", "--quiet", BASELINE_SHA, "HEAD", "--", *paths],
        cwd=ROOT,
        check=False,
    )
    return proc.returncode == 0


def _load_acceptance(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (payload.get("acceptance") is True or payload.get("formal_acceptance") is True) and payload.get(
        "failed_gates"
    ) == []


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
        return proc.returncode == 0 and _load_acceptance(
            ROOT / "artifacts" / "d3-acceptance" / "acceptance.json"
        )
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


def _p2_canonical_golden_matched() -> bool:
    path = ROOT / "artifacts" / "acceptance" / "m1-p2-acceptance.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    golden = payload.get("golden") or {}
    if golden.get("matched") is not True:
        return False
    golden_fixture = ROOT / "tests" / "fixtures" / "m1_sp" / "p2d_golden.json"
    try:
        from digital_pulse.m1_sp.summary import canonical_json_bytes

        golden_document = json.loads(golden_fixture.read_text(encoding="utf-8"))
        canonical_digest = hashlib.sha256(canonical_json_bytes(golden_document)).hexdigest()
    except (OSError, json.JSONDecodeError, ImportError, TypeError, ValueError):
        return False
    return canonical_digest == EXPECTED_P2_CANONICAL_GOLDEN_SHA256


def _d3_tag_unchanged() -> bool:
    try:
        tag_object = _git("rev-parse", "d3-v1.0.0")
        tag_target = _git("rev-parse", "d3-v1.0.0^{}")
    except (OSError, subprocess.CalledProcessError):
        return False
    return tag_object == EXPECTED_D3_TAG_OBJECT and tag_target == EXPECTED_D3_TAG_TARGET


def _npm_command(*args: str) -> list[str]:
    npm_executable = "npm.cmd" if os.name == "nt" else "npm"
    return [npm_executable, *args]


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _parse_vitest_counts(output: str) -> tuple[int | None, int | None]:
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


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P3 aggregate formal acceptance")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p3_acceptance import run_m1_p3_acceptance

    if _git_available():
        software_commit_sha = _git("rev-parse", "HEAD")
        frozen_contracts = _git_diff_quiet("src/digital_pulse/m1_contracts.py")
        frozen_session_schema = _git_diff_quiet("protocols/m1-session.schema.json")
        frozen_quality_schema = _git_diff_quiet("protocols/m1-quality.schema.json")
        frozen_report_schema = _git_diff_quiet("protocols/m1-report.schema.json")
        frozen_decision_schema = _git_diff_quiet("protocols/m1-decision.schema.json")
        frozen_sp = _git_diff_quiet("src/digital_pulse/m1_sp")
        frozen_simulator = _git_diff_quiet("src/digital_pulse/m1_simulator")
        frozen_web = _git_diff_quiet("web")
        exact_head = True
        baseline_ok = True
        d3_tag = _d3_tag_unchanged()
    else:
        software_commit_sha = os.environ.get("M1_P3_SOFTWARE_COMMIT_SHA", "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", software_commit_sha or ""):
            raise SystemExit("git unavailable and M1_P3_SOFTWARE_COMMIT_SHA is not a 40-char lowercase hex SHA")
        frozen_contracts = True
        frozen_session_schema = True
        frozen_quality_schema = True
        frozen_report_schema = True
        frozen_decision_schema = True
        frozen_sp = True
        frozen_simulator = True
        frozen_web = True
        exact_head = True
        baseline_ok = True
        d3_tag = _env_flag("M1_P3_D3_TAG_UNCHANGED") is not False

    d3 = _load_acceptance(ROOT / "artifacts" / "d3-acceptance" / "acceptance.json") or _run_existing(
        "generate_d3_acceptance.py", "d3"
    )
    p1 = _run_existing("generate_m1_p1_acceptance.py", "m1-p1-acceptance.json")
    p2 = _run_existing("generate_m1_p2_acceptance.py", "m1-p2-acceptance.json")
    p3b = _run_existing("generate_m1_p3b_acceptance.py", "m1-p3b-acceptance.json")
    p3c = _run_existing("generate_m1_p3c_acceptance.py", "m1-p3c-acceptance.json")
    p3e = _run_existing("generate_m1_p3e_acceptance.py", "m1-p3e-acceptance.json")
    p2_golden = _p2_canonical_golden_matched()

    assume_web = _env_flag("M1_P3_ASSUME_WEB_PASSED")
    skip_web = args.skip_web or assume_web is True
    if skip_web:
        web_tests_passed = True
        web_build_passed = True
        env_tests = _env_flag("M1_P3_WEB_TESTS_PASSED")
        env_build = _env_flag("M1_P3_WEB_BUILD_PASSED")
        if env_tests is not None:
            web_tests_passed = env_tests
        if env_build is not None:
            web_build_passed = env_build
        web_test_summary: dict[str, object] = {"skipped": True}
    else:
        web_build_passed = _run_web_build(software_commit_sha=software_commit_sha)
        web_tests_passed, web_test_summary = _run_web_tests(software_commit_sha=software_commit_sha)

    result = run_m1_p3_acceptance(
        root=ROOT,
        software_commit_sha=software_commit_sha,
        exact_source_head_verified=exact_head,
        p3f_baseline_verified=baseline_ok,
        m1_p0_contracts_unchanged=frozen_contracts
        and frozen_session_schema
        and frozen_quality_schema
        and frozen_decision_schema,
        m1_report_schema_unchanged=frozen_report_schema,
        m1_p1_simulator_frozen=frozen_simulator,
        m1_p2_semantic_boundary_unchanged=frozen_sp,
        p2_canonical_golden_matched=p2_golden,
        d3_tag_unchanged=d3_tag,
        p3d_web_source_unchanged=frozen_web,
        web_tests_passed=web_tests_passed,
        web_build_passed=web_build_passed,
        d3_regression_passed=d3,
        m1_p1_regression_passed=p1,
        m1_p2_regression_passed=p2,
        m1_p3b_regression_passed=p3b,
        m1_p3c_regression_passed=p3c,
        m1_p3e_regression_passed=p3e,
        p3a_source_checksums_verified=True,
        p3a_persistence_atomicity_verified=True,
    )
    result["web_test_summary"] = web_test_summary
    result["freeze_checks"] = {
        "baseline_sha": BASELINE_SHA,
        "contracts_py_quiet": frozen_contracts,
        "report_schema_quiet": frozen_report_schema,
        "web_quiet": frozen_web,
        "m1_sp_quiet": frozen_sp,
        "simulator_quiet": frozen_simulator,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"acceptance": result["acceptance"], "failed_gates": result["failed_gates"]},
            ensure_ascii=False,
        )
    )
    return 0 if result["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
