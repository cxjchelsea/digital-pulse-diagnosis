"""P3F Final Review 反例：假绿路径必须失败关闭。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from digital_pulse.m1_p3_acceptance import (
    FIXED_SOFTWARE_COMMIT_SHA,
    P3F_BASELINE_SHA,
    _concurrent_same_run_ok,
    _direct_and_replay_bundle,
    _non_hex_fingerprint_rejected,
    _persistence_atomicity_fail_closed,
    _report_tamper_distinction,
    _scenario_overrides,
    _source_checksums_fail_closed,
    compact_case_summary,
    web_regression_claim,
)
from digital_pulse.m1_app import AppSessionLoader
from digital_pulse.m1_simulator import get_attempt_plan, get_scenario


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, encoding="utf-8").strip()


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=review@example.com", "-c", "user.name=review", "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_golden_generation_from_non_baseline_head_fails(tmp_path: Path):
    """非基线 HEAD 不得生成 golden。"""

    _init_git_repo(tmp_path)
    actual_head = _git(tmp_path, "rev-parse", "HEAD")
    assert actual_head != P3F_BASELINE_SHA
    golden = _load_script("generate_m1_p3_golden.py")
    with pytest.raises(SystemExit, match="refuse golden generation"):
        golden.require_exact_git_head(P3F_BASELINE_SHA, repo_root=tmp_path)


def test_golden_generation_fails_closed_without_git(tmp_path: Path):
    """git 不可用时必须失败关闭，禁止环境变量静默宣称基线。"""

    golden = _load_script("generate_m1_p3_golden.py")
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    with pytest.raises(SystemExit, match="git unavailable"):
        golden.require_exact_git_head(P3F_BASELINE_SHA, repo_root=missing)


def test_stale_p3b_artifact_cannot_satisfy_current_head(tmp_path: Path):
    generator = _load_script("generate_m1_p3_acceptance.py")
    path = tmp_path / "m1-p3b-acceptance.json"
    path.write_text(
        json.dumps({"acceptance": True, "failed_gates": [], "software_commit_sha": "deadbeef" * 5}),
        encoding="utf-8",
    )
    assert generator.load_acceptance(path, expected_software_sha="c" * 40, require_head_sha=True) is False


def test_stale_p3c_artifact_cannot_satisfy_current_head(tmp_path: Path):
    generator = _load_script("generate_m1_p3_acceptance.py")
    path = tmp_path / "m1-p3c-acceptance.json"
    path.write_text(
        json.dumps({"acceptance": True, "failed_gates": [], "software_commit_sha": "ab" * 20}),
        encoding="utf-8",
    )
    assert generator.load_acceptance(path, expected_software_sha="cd" * 20, require_head_sha=True) is False


def test_stale_p3e_artifact_cannot_satisfy_current_head(tmp_path: Path):
    generator = _load_script("generate_m1_p3_acceptance.py")
    path = tmp_path / "m1-p3e-acceptance.json"
    path.write_text(
        json.dumps({"acceptance": True, "failed_gates": [], "software_commit_sha": "11" * 20}),
        encoding="utf-8",
    )
    assert generator.load_acceptance(path, expected_software_sha="22" * 20, require_head_sha=True) is False


def test_stale_p2_artifact_cannot_satisfy_current_head(tmp_path: Path):
    generator = _load_script("generate_m1_p3_acceptance.py")
    path = tmp_path / "m1-p2-acceptance.json"
    path.write_text(
        json.dumps({"acceptance": True, "failed_gates": [], "software_commit_sha": "33" * 20}),
        encoding="utf-8",
    )
    assert generator.load_acceptance(path, expected_software_sha="44" * 20, require_head_sha=True) is False


def test_p1_without_sha_field_is_not_forced_to_head_bound(tmp_path: Path):
    """P1/D3 历史 schema 没有 software_commit_sha 时不得被一刀切拒绝。"""

    generator = _load_script("generate_m1_p3_acceptance.py")
    path = tmp_path / "m1-p1-acceptance.json"
    path.write_text(json.dumps({"acceptance": True, "failed_gates": []}), encoding="utf-8")
    assert generator.load_acceptance(path, expected_software_sha="c" * 40, require_head_sha=False) is True


def test_baseline_ancestry_mismatch_fails(tmp_path: Path):
    _init_git_repo(tmp_path)
    _git(tmp_path, "checkout", "--orphan", "unrelated")
    subprocess.run(
        ["git", "-c", "user.email=review@example.com", "-c", "user.name=review", "commit", "--allow-empty", "-m", "unrelated"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    generator = _load_script("generate_m1_p3_acceptance.py")
    head_sha = _git(tmp_path, "rev-parse", "HEAD")
    evidence = generator.baseline_ancestry_verified(
        repo_root=tmp_path,
        head_sha=head_sha,
        pr_base_sha=None,
    )
    assert evidence["verified"] is False
    assert evidence["baseline_is_ancestor_of_head"] is False


def test_web_assumption_cannot_masquerade_as_embedded_success():
    generator = _load_script("generate_m1_p3_acceptance.py")
    skipped = generator.resolve_web_evidence(skip_web=True, assume_web_passed=None)
    assumed = generator.resolve_web_evidence(skip_web=False, assume_web_passed=True)
    assert skipped["mode"] == "external_required"
    assert skipped["embedded"] is False
    assert skipped["web_regression_passed"] is False
    assert assumed["web_regression_passed"] is False
    assert web_regression_claim(mode="external_required", tests_passed=True, build_passed=True) is False
    assert web_regression_claim(mode="embedded", tests_passed=True, build_passed=True) is True


def test_actual_concurrent_duplicate_run_handling(tmp_path: Path):
    evidence = _concurrent_same_run_ok(tmp_path, software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    assert evidence["passed"] is True
    assert evidence["overlapping_writers"] is True
    assert evidence["same_run_id"] is True
    assert evidence["run_ids"] == ["run-concurrent-dup"]


def test_multi_attempt_sessions_are_independent(tmp_path: Path):
    plan = get_attempt_plan("retry_improves", random_seed=1001, duration_s=8.0, sample_rate_hz=250.0)
    identities = []
    snapshots = []
    for attempt in plan.attempts:
        case_root = tmp_path / f"attempt-{attempt.attempt_index}"
        case_root.mkdir()
        bundle = _direct_and_replay_bundle(
            case_root,
            attempt.config,
            software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        )
        identities.append(bundle["recorded"].session_id)
        snapshots.append(
            {
                "path": bundle["recorded"].session_path,
                "bytes": {
                    str(path.relative_to(bundle["recorded"].session_path)): path.read_bytes()
                    for path in bundle["recorded"].session_path.rglob("*")
                    if path.is_file()
                },
            }
        )
    assert len(set(identities)) == len(identities) == 2
    # 后续 attempt 不得改写先前会话字节
    for snapshot in snapshots:
        current = {
            str(path.relative_to(snapshot["path"])): path.read_bytes()
            for path in snapshot["path"].rglob("*")
            if path.is_file()
        }
        assert current == snapshot["bytes"]


def test_raw_persistence_failure_creates_no_complete_app_run(tmp_path: Path):
    bundle = _direct_and_replay_bundle(
        tmp_path,
        get_scenario("raw_persistence_failure", **_scenario_overrides("raw_persistence_failure")),
        software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
    )
    summary = compact_case_summary(
        scenario_id="raw_persistence_failure",
        attempt_index=1,
        session=bundle["session"],
        analysis=bundle["replay_app"],
        report_payload=bundle["replay_report"],
    )
    loaded = AppSessionLoader(tmp_path).load(bundle["recorded"].session_id)
    complete_runs = [run for run in loaded.app_manifest.runs if run.state.value == "complete"]
    assert summary["raw_persistence_status"] == "failed"
    assert summary["analysis_allowed"] is False
    assert summary["formal_parameters_is_null"] is True
    assert summary["report_status"] == "incomplete"
    assert summary["session_completed"] is False
    assert loaded.session.session_id == bundle["recorded"].session_id
    assert complete_runs == []


def test_report_checksum_tamper_distinction_still_works(tmp_path: Path):
    evidence = _report_tamper_distinction(tmp_path, software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    assert evidence["bytes_only_ok"] is True
    assert evidence["semantic_ok"] is True


def test_source_checksum_and_atomicity_and_fingerprint(tmp_path: Path):
    checksum = _source_checksums_fail_closed(tmp_path / "checksum", software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    atomic = _persistence_atomicity_fail_closed(tmp_path / "atomic", software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    fingerprint = _non_hex_fingerprint_rejected(tmp_path / "fp", software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    assert checksum["passed"] is True
    assert atomic["passed"] is True
    assert fingerprint["passed"] is True
