"""Formal M1-P3B APP replay/projection/gate acceptance."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from digital_pulse.m1_app import (
    AppSessionLoader,
    ReplayAnalysisService,
    compare_app_analysis,
)
from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    SimulatorDataSource,
    get_attempt_plan,
    get_scenario,
    list_attempt_plans,
    list_scenarios,
    list_simulation_cases,
)
from digital_pulse.m1_sp import (
    P2C_CONFIGURATION_DIGEST,
    SP_RESULT_FINGERPRINT_VERSION,
    SPProcessor,
    compare_sp_results,
)
from digital_pulse.m1_sp.models import SPProcessingProvenance

ACCEPTANCE_FORMAT_VERSION = "m1-p3b-acceptance-v1"
ACCEPTANCE_SEED = 1001
ACCEPTANCE_DURATION_S = 8.0
ACCEPTANCE_INSUFFICIENT_DURATION_S = 1.2
ACCEPTANCE_SAMPLE_RATE_HZ = 250.0


def run_m1_p3b_acceptance(
    *,
    software_commit_sha: str,
    workspace_clean: bool,
    frozen_baselines: Mapping[str, Mapping[str, Any]] | None = None,
    d3_regression_passed: bool = True,
    m1_p1_regression_passed: bool = True,
    m1_p2_regression_passed: bool = True,
) -> dict[str, Any]:
    gates: dict[str, bool] = {}
    single: dict[str, Any] = {}
    multi: dict[str, Any] = {}
    direct_replay_sp: list[bool] = []
    direct_replay_app: list[bool] = []
    determinism: list[bool] = []
    oracle_delete: list[bool] = []
    oracle_tamper: list[bool] = []
    gate_matrix: list[bool] = []
    formal_block_matrix: list[bool] = []
    read_only_checks: list[bool] = []
    persisted_checks: list[bool] = []

    with tempfile.TemporaryDirectory(prefix="m1-p3b-acceptance-") as temporary:
        root = Path(temporary)
        for scenario_id in list_scenarios():
            outcome = _process_case(
                root,
                scenario_id,
                get_scenario(scenario_id, **_overrides(scenario_id)),
                software_commit_sha,
            )
            single[scenario_id] = outcome["summary"]
            direct_replay_sp.append(outcome["direct_replay_sp"])
            direct_replay_app.append(outcome["direct_replay_app"])
            determinism.append(outcome["deterministic"])
            oracle_delete.append(outcome["oracle_delete"])
            oracle_tamper.append(outcome["oracle_tamper"])
            gate_matrix.append(outcome["gate_valid"])
            formal_block_matrix.append(outcome["formal_block_valid"])
            read_only_checks.append(outcome["read_only_unchanged"])

        for plan_id in list_attempt_plans():
            plan = get_attempt_plan(
                plan_id,
                random_seed=ACCEPTANCE_SEED,
                duration_s=ACCEPTANCE_DURATION_S,
                sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
            )
            attempts = []
            for attempt in plan.attempts:
                outcome = _process_case(
                    root,
                    f"{plan_id}-attempt-{attempt.attempt_index:02d}",
                    attempt.config,
                    software_commit_sha,
                )
                attempts.append(outcome["summary"])
                direct_replay_sp.append(outcome["direct_replay_sp"])
                direct_replay_app.append(outcome["direct_replay_app"])
                determinism.append(outcome["deterministic"])
                oracle_delete.append(outcome["oracle_delete"])
                oracle_tamper.append(outcome["oracle_tamper"])
                gate_matrix.append(outcome["gate_valid"])
                formal_block_matrix.append(outcome["formal_block_valid"])
                read_only_checks.append(outcome["read_only_unchanged"])
            multi[plan_id] = {"attempt_count": len(attempts), "attempts": attempts}

        persisted_checks.append(_persisted_replay_check(root, software_commit_sha))
        raw_tamper_ok = _raw_tamper_check(root, software_commit_sha)

    frozen = dict(frozen_baselines or {})
    gates.update(
        {
            "workspace_clean": bool(workspace_clean),
            "scenario_registry_exact_16_plus_2": len(list_scenarios()) == 16
            and len(list_attempt_plans()) == 2
            and len(list_simulation_cases()) == 18,
            "direct_replay_sp_equivalent": all(direct_replay_sp),
            "direct_replay_app_equivalent": all(direct_replay_app),
            "determinism": all(determinism),
            "oracle_delete_verified": all(oracle_delete),
            "oracle_tamper_verified": all(oracle_tamper),
            "quality_gate_matrix": all(gate_matrix),
            "formal_parameter_block_matrix": all(formal_block_matrix),
            "read_only_replay_unchanged": all(read_only_checks),
            "persisted_replay_integrity": all(persisted_checks),
            "raw_tamper_fail_closed": raw_tamper_ok,
            "sp_fingerprint_version": SP_RESULT_FINGERPRINT_VERSION == "sp-result-fingerprint:v2",
            "sp_parameter_digest_unchanged": P2C_CONFIGURATION_DIGEST
            == "b71d02832551f5236f34ecb3ce866bb50df3420530fd3bfc8b0b17a583274371",
            "d3_regression_passed": bool(d3_regression_passed),
            "m1_p1_regression_passed": bool(m1_p1_regression_passed),
            "m1_p2_regression_passed": bool(m1_p2_regression_passed),
        }
    )
    for key, detail in frozen.items():
        gates[f"{key}_frozen"] = detail.get("state") == "unchanged"
    failed = [key for key, passed in gates.items() if not passed]
    return {
        "format_version": ACCEPTANCE_FORMAT_VERSION,
        "acceptance": not failed,
        "formal_acceptance": not failed,
        "failed_gates": failed,
        "software_commit_sha": software_commit_sha,
        "single_attempt_cases": len(single),
        "multi_attempt_cases": len(multi),
        "attempt_count": len(direct_replay_sp),
        "direct_replay_sp_equivalent": all(direct_replay_sp),
        "direct_replay_app_equivalent": all(direct_replay_app),
        "determinism_verified": all(determinism),
        "oracle_isolation_verified": all(oracle_delete) and all(oracle_tamper),
        "read_only_replay_unchanged": all(read_only_checks),
        "persisted_replay_integrity": all(persisted_checks),
        "raw_tamper_fail_closed": raw_tamper_ok,
        "sp_semantic_fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "sp_parameter_digest": P2C_CONFIGURATION_DIGEST,
        "single_attempt": single,
        "multi_attempt": multi,
        "frozen_baselines": frozen,
        "gates": gates,
        "limitations": [
            "synthetic simulator evidence only",
            "pending H1 calibration; formal parameters remain unavailable",
            "no REST API, React UI, report builder, INT decision, hardware, or medical claim",
        ],
    }


def _overrides(scenario_id: str) -> dict[str, Any]:
    return {
        "random_seed": ACCEPTANCE_SEED,
        "sample_rate_hz": ACCEPTANCE_SAMPLE_RATE_HZ,
        "duration_s": (
            ACCEPTANCE_INSUFFICIENT_DURATION_S
            if scenario_id == "insufficient_duration"
            else ACCEPTANCE_DURATION_S
        ),
    }


def _process_case(root: Path, case_id: str, config, software_commit_sha: str) -> dict[str, Any]:
    case_root = root / f"case-{case_id}"
    case_root.mkdir(parents=True, exist_ok=False)
    recorded = M1SessionRecorder(software_commit_sha=software_commit_sha).record(
        SimulatorDataSource(config),
        output_root=case_root,
    )
    AppSessionLoader(case_root).register(recorded.session_id)
    manifest_before = (recorded.session_path / "app" / "manifest.json").read_bytes()
    service = ReplayAnalysisService(case_root)
    replayed = service.replay(recorded.session_id, software_commit_sha=software_commit_sha)
    replayed_again = service.replay(recorded.session_id, software_commit_sha=software_commit_sha)

    direct_samples = list(SimulatorDataSource(config).samples())[: recorded.sample_count]
    from digital_pulse.m1_simulator.replay import ReplayDataSource

    replay_source = ReplayDataSource(recorded.session_path, allow_incomplete=not recorded.completed)
    direct = SPProcessor().process(
        replay_source.session,
        direct_samples,
        provenance=SPProcessingProvenance(software_commit_sha=software_commit_sha),
    )
    from digital_pulse.m1_app.analysis import AnalysisProjector, create_replay_app_provenance

    direct_app = AnalysisProjector().project(
        session=replay_source.session,
        sp_result=direct,
        app_provenance=create_replay_app_provenance(software_commit_sha),
    )

    deleted = _oracle_variant(recorded.session_path, root, f"{case_id}-deleted", delete=True)
    tampered = _oracle_variant(recorded.session_path, root, f"{case_id}-tampered", delete=False)
    deleted_result = ReplayAnalysisService(deleted.parent).replay(recorded.session_id, software_commit_sha=software_commit_sha)
    tampered_result = ReplayAnalysisService(tampered.parent).replay(recorded.session_id, software_commit_sha=software_commit_sha)

    gate = replayed.analysis.gate
    quality_label = None if replayed.analysis.quality is None else replayed.analysis.quality["label"]
    gate_valid = (
        (quality_label == "acceptable" and gate.analysis_allowed is True)
        or (quality_label != "acceptable" and gate.analysis_allowed is False)
    )
    if replayed.sp_result.processing_status == "blocked_before_quality":
        gate_valid = gate_valid and replayed.analysis.quality is None
    return {
        "direct_replay_sp": compare_sp_results(direct, replayed.sp_result),
        "direct_replay_app": compare_app_analysis(direct_app, replayed.analysis),
        "deterministic": compare_sp_results(replayed.sp_result, replayed_again.sp_result)
        and compare_app_analysis(replayed.analysis, replayed_again.analysis),
        "oracle_delete": compare_app_analysis(replayed.analysis, deleted_result.analysis)
        and compare_sp_results(replayed.sp_result, deleted_result.sp_result),
        "oracle_tamper": compare_app_analysis(replayed.analysis, tampered_result.analysis)
        and compare_sp_results(replayed.sp_result, tampered_result.sp_result),
        "gate_valid": gate_valid,
        "formal_block_valid": gate.formal_parameters_allowed is False
        and replayed.analysis.formal_parameters is None,
        "read_only_unchanged": (recorded.session_path / "app" / "manifest.json").read_bytes() == manifest_before,
        "summary": {
            "case_id": case_id,
            "session_id": recorded.session_id,
            "completed": recorded.completed,
            "sample_count": recorded.sample_count,
            "sp_status": replayed.sp_result.processing_status,
            "quality_label": quality_label,
            "analysis_allowed": gate.analysis_allowed,
            "formal_parameters_allowed": gate.formal_parameters_allowed,
            "blocking_codes": list(gate.blocking_codes),
            "sp_result_sha256": replayed.sp_result.result_sha256,
            "app_analysis_sha256": replayed.analysis.semantic_fingerprint_sha256,
        },
    }


def _oracle_variant(session_path: Path, root: Path, name: str, *, delete: bool) -> Path:
    parent = root / name
    target = parent / session_path.name
    shutil.copytree(session_path, target)
    if delete:
        (target / "scenario.json").unlink()
        (target / "expected.json").unlink()
    else:
        path = target / "expected.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["expected_quality_label"] = "tampered"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return target


def _persisted_replay_check(root: Path, software_commit_sha: str) -> bool:
    check_root = root / "persisted-replay-check"
    check_root.mkdir(parents=True, exist_ok=False)
    recorded = M1SessionRecorder(software_commit_sha=software_commit_sha).record(
        SimulatorDataSource(get_scenario("normal_high_quality", **_overrides("normal_high_quality"))),
        output_root=check_root,
    )
    AppSessionLoader(check_root).register(recorded.session_id)
    service = ReplayAnalysisService(check_root)
    service.replay(recorded.session_id, software_commit_sha=software_commit_sha, persist=True, run_id="run-p3b")
    loaded = AppSessionLoader(check_root).load(recorded.session_id)
    if loaded.app_manifest.current_run_id != "run-p3b":
        return False
    run = loaded.app_manifest.runs[0]
    roles = {asset.role.value for asset in run.assets}
    return {"sp_result", "sp_series", "analysis", "provenance", "checksums"}.issubset(roles)


def _raw_tamper_check(root: Path, software_commit_sha: str) -> bool:
    check_root = root / "raw-tamper-check"
    check_root.mkdir(parents=True, exist_ok=False)
    recorded = M1SessionRecorder(software_commit_sha=software_commit_sha).record(
        SimulatorDataSource(get_scenario("normal_high_quality", **_overrides("normal_high_quality"))),
        output_root=check_root,
    )
    AppSessionLoader(check_root).register(recorded.session_id)
    samples = recorded.session_path / "samples.jsonl"
    samples.write_text(samples.read_text(encoding="utf-8").replace('"frame_sequence":0', '"frame_sequence":9', 1), encoding="utf-8")
    try:
        ReplayAnalysisService(check_root).replay(recorded.session_id, software_commit_sha=software_commit_sha)
    except Exception as exc:
        return getattr(exc, "code", None) == "raw_asset_corrupted"
    return False
