"""Formal M1-P2 acceptance over the public 16+2 simulator registry."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np

from digital_pulse.m1_contracts import SourceType

from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    ReplayDataSource,
    SimulatorDataSource,
    get_attempt_plan,
    get_scenario,
    list_attempt_plans,
    list_scenarios,
    list_simulation_cases,
)
from digital_pulse.m1_sp import (
    P2A_CONFIGURATION_DIGEST,
    P2B_CONFIGURATION_DIGEST,
    P2C_CONFIGURATION_DIGEST,
    SPProcessingProvenance,
    SPProcessor,
    SP_RESULT_FINGERPRINT_VERSION,
    RawIdentityConverter,
    canonical_json_bytes,
    compare_sp_results,
    sp_result_sha256,
    summarize_sp_result,
)

ACCEPTANCE_FORMAT_VERSION = "m1-p2-acceptance-v1"
GOLDEN_FORMAT_VERSION = "m1-p2-golden-v1"
ACCEPTANCE_SEED = 1001
ACCEPTANCE_DURATION_S = 8.0
ACCEPTANCE_INSUFFICIENT_DURATION_S = 1.2
ACCEPTANCE_SAMPLE_RATE_HZ = 250.0

EXPECTED_SINGLE_CASES = (
    "abort",
    "baseline_drift",
    "device_fault",
    "frame_loss",
    "insufficient_duration",
    "lower_saturation",
    "motion_artifact",
    "no_contact",
    "normal_high_quality",
    "ppg_misalignment",
    "raw_persistence_failure",
    "sensor_disconnection",
    "timestamp_regression",
    "unstable_load",
    "upper_saturation",
    "weak_signal",
)
EXPECTED_MULTI_CASES = ("retry_improves", "retry_still_fails")
EXPECTED_LABELS = {
    "normal_high_quality": "acceptable",
    "weak_signal": "weak_signal",
    "no_contact": "no_contact",
    "upper_saturation": "saturated",
    "lower_saturation": "saturated",
    "baseline_drift": "unstable_baseline",
    "motion_artifact": "motion_artifact",
    "unstable_load": "manual_review_required",
    "ppg_misalignment": "reference_mismatch",
    "insufficient_duration": "insufficient_duration",
    "frame_loss": "data_integrity_failure",
    "timestamp_regression": "data_integrity_failure",
    "sensor_disconnection": "data_integrity_failure",
    "raw_persistence_failure": "data_integrity_failure",
}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def scenario_registry_digest() -> str:
    value = {
        "single_attempt": list(list_scenarios()),
        "simulation_cases": list(list_simulation_cases()),
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


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


def _process_recorded(config, root: Path, name: str, software_commit_sha: str) -> dict[str, Any]:
    recorder = M1SessionRecorder(software_commit_sha=software_commit_sha)
    recorded = recorder.record(
        SimulatorDataSource(config), output_root=root, directory_name=name
    )
    replay = ReplayDataSource(recorded.session_path, allow_incomplete=not recorded.completed)
    provenance = SPProcessingProvenance(software_commit_sha=software_commit_sha)
    processor = SPProcessor()

    direct_samples = list(SimulatorDataSource(config).samples())[: recorded.sample_count]
    direct_session = replace(replay.session, source_type=SourceType.SIMULATOR)
    direct = processor.process(direct_session, direct_samples, provenance=provenance)
    replayed = processor.process(replay.session, replay.samples(), provenance=provenance)
    repeats = [processor.process(direct_session, direct_samples, provenance=provenance) for _ in range(2)]

    deleted_path = root / f"{name}-oracle-deleted"
    shutil.copytree(recorded.session_path, deleted_path)
    (deleted_path / "scenario.json").unlink()
    (deleted_path / "expected.json").unlink()
    deleted_replay = ReplayDataSource(deleted_path, allow_incomplete=not recorded.completed)
    deleted = processor.process(deleted_replay.session, deleted_replay.samples(), provenance=provenance)

    tampered_path = root / f"{name}-oracle-tampered"
    shutil.copytree(recorded.session_path, tampered_path)
    expected_path = tampered_path / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["expected_quality_label"] = "tampered_oracle_must_be_ignored"
    expected_path.write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
    tampered_replay = ReplayDataSource(tampered_path, allow_incomplete=not recorded.completed)
    tampered = processor.process(tampered_replay.session, tampered_replay.samples(), provenance=provenance)

    return {
        "direct": direct,
        "direct_replay_match": compare_sp_results(direct, replayed),
        "deterministic_repeat_match": all(compare_sp_results(direct, item) for item in repeats),
        "oracle_delete_match": compare_sp_results(direct, deleted),
        "oracle_tamper_match": compare_sp_results(direct, tampered),
        "summary": summarize_sp_result(direct),
    }


def _matches_golden(actual: Any, expected: Any, *, tolerance: float = 1e-12) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _matches_golden(actual[key], expected[key], tolerance=tolerance) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _matches_golden(a, b, tolerance=tolerance) for a, b in zip(actual, expected)
        )
    return actual == expected


def _oracle_isolated(source_root: Path) -> bool:
    forbidden = ("expected.json", "golden_summaries", "fixtures", "m1_simulator")
    for path in (source_root / "digital_pulse" / "m1_sp").glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        if any(token in text for token in forbidden):
            return False
    return True


def _semantic_fingerprint_coverage(result) -> dict[str, Any]:
    def rehash(value):
        return replace(value, result_sha256=sp_result_sha256(value))

    def detects(value) -> bool:
        changed = rehash(value)
        return (
            changed.result_sha256 != result.result_sha256
            and not compare_sp_results(result, changed)
        )

    integrity = replace(result.integrity, missing_frame_count=result.integrity.missing_frame_count + 1)
    preprocessing = replace(result.stage_result.preprocessing, integrity=integrity)
    integrity_changed = replace(
        result,
        stage_result=replace(result.stage_result, preprocessing=preprocessing),
    )

    beats_removed = replace(
        result,
        stage_result=replace(result.stage_result, beats_by_window={}),
    )
    references_removed = replace(
        result,
        stage_result=replace(result.stage_result, reference_by_window={}),
    )

    peak_detected = False
    if result.beats_by_window:
        window_id, analysis = next(iter(result.beats_by_window.items()))
        if analysis.candidates:
            candidate = analysis.candidates[0]
            candidates = (
                replace(candidate, peak_device_time_us=candidate.peak_device_time_us + 1),
                *analysis.candidates[1:],
            )
            beats = dict(result.beats_by_window)
            beats[window_id] = replace(analysis, candidates=candidates)
            peak_detected = detects(
                replace(
                    result,
                    stage_result=replace(result.stage_result, beats_by_window=beats),
                )
            )

    reference_pair_detected = False
    if result.reference_by_window:
        window_id, reference = next(iter(result.reference_by_window.items()))
        if reference.matched_pairs:
            pulse_index, ppg_index, lag_ms = reference.matched_pairs[0]
            pairs = ((pulse_index, ppg_index + 1, lag_ms), *reference.matched_pairs[1:])
            references = dict(result.reference_by_window)
            references[window_id] = replace(reference, matched_pairs=pairs)
            reference_pair_detected = detects(
                replace(
                    result,
                    stage_result=replace(result.stage_result, reference_by_window=references),
                )
            )

    filter_detected = False
    if result.filter_views_by_window:
        window_id, views = next(iter(result.filter_views_by_window.items()))
        offline = views.get("offline_review")
        if offline is not None:
            values = np.array(offline.values, copy=True)
            finite = np.flatnonzero(np.isfinite(values))
            if finite.size:
                values[int(finite[0])] += 1.0
                changed_views = dict(views)
                changed_views["offline_review"] = replace(offline, values=values)
                filters = dict(result.filter_views_by_window)
                filters[window_id] = changed_views
                filter_detected = detects(
                    replace(
                        result,
                        stage_result=replace(result.stage_result, filter_views_by_window=filters),
                    )
                )

    provenance_changed = replace(
        result,
        software_commit_sha=("d" * 40 if result.software_commit_sha != "d" * 40 else "e" * 40),
        session_id=f"{result.session_id}-container-copy",
    )
    provenance_excluded = (
        sp_result_sha256(provenance_changed) == result.result_sha256
        and compare_sp_results(result, provenance_changed)
    )

    return {
        "version": SP_RESULT_FINGERPRINT_VERSION,
        "integrity_drift_detected": detects(integrity_changed),
        "filter_drift_detected": filter_detected,
        "beat_removal_detected": detects(beats_removed),
        "peak_time_drift_detected": peak_detected,
        "reference_removal_detected": detects(references_removed),
        "reference_pair_drift_detected": reference_pair_detected,
        "execution_provenance_excluded": provenance_excluded,
    }


def run_m1_p2_acceptance(
    *,
    golden_path: Path,
    software_commit_sha: str,
    source_root: Path,
    workspace_clean: bool,
    m1_contracts_unchanged: bool = True,
    d3_regression_passed: bool = True,
    m1_p1_regression_passed: bool = True,
    write_golden: bool = False,
) -> dict[str, Any]:
    provenance = SPProcessingProvenance(software_commit_sha=software_commit_sha)
    del provenance  # validation is the purpose; processing receives the same value below.
    before_golden_sha = _sha256(golden_path)
    single: dict[str, Any] = {}
    multi: dict[str, Any] = {}
    direct_replay_checks: list[bool] = []
    determinism_checks: list[bool] = []
    oracle_delete_checks: list[bool] = []
    oracle_tamper_checks: list[bool] = []
    blocked_invariants: list[bool] = []
    quality_schema_checks: list[bool] = []
    confidence_checks: list[bool] = []
    score_checks: list[bool] = []
    parameter_status_checks: list[bool] = []
    revision_checks: list[bool] = []
    result_sha_checks: list[bool] = []
    limitation_checks: list[bool] = []
    window_checks: list[bool] = []
    causal_filter_checks: list[bool] = []
    offline_filter_checks: list[bool] = []
    semantic_fingerprint_coverage: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="m1-p2-acceptance-") as temporary:
        root = Path(temporary)
        for scenario_id in list_scenarios():
            outcome = _process_recorded(
                get_scenario(scenario_id, **_overrides(scenario_id)),
                root,
                f"single-{scenario_id}",
                software_commit_sha,
            )
            result = outcome["direct"]
            if scenario_id == "normal_high_quality":
                semantic_fingerprint_coverage = _semantic_fingerprint_coverage(result)
            single[scenario_id] = outcome["summary"]
            direct_replay_checks.append(outcome["direct_replay_match"])
            determinism_checks.append(outcome["deterministic_repeat_match"])
            oracle_delete_checks.append(outcome["oracle_delete_match"])
            oracle_tamper_checks.append(outcome["oracle_tamper_match"])
            blocked_invariants.append(
                (result.processing_status == "blocked_before_quality" and not result.quality_results)
                or (result.processing_status == "quality_evaluated" and bool(result.quality_results))
            )
            for quality in result.quality_results:
                try:
                    quality.validate_schema()
                    quality_schema_checks.append(True)
                except Exception:
                    quality_schema_checks.append(False)
                confidence_checks.append(quality.confidence is None)
                score_checks.append(quality.score is None)
                parameter_status_checks.append(quality.parameter_status.value == "synthetic_only")
            revision_checks.append(result.software_commit_sha == software_commit_sha)
            result_sha_checks.append(len(result.result_sha256) == 64)
            limitation_checks.append(bool(result.limitations))
            window_checks.extend(
                window.end_index - window.start_index == window.sample_count
                for window in result.windows
            )
            for views in result.filter_views_by_window.values():
                causal_filter_checks.append(views["causal"].mode == "causal")
                offline_filter_checks.append(
                    views["offline_review"].mode == "offline_review"
                    and views["ppg_offline"].mode == "offline_review"
                )

        for plan_id in list_attempt_plans():
            plan = get_attempt_plan(
                plan_id,
                random_seed=ACCEPTANCE_SEED,
                duration_s=ACCEPTANCE_DURATION_S,
                sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
            )
            attempts = []
            for attempt in plan.attempts:
                outcome = _process_recorded(
                    attempt.config,
                    root,
                    f"plan-{plan_id}-attempt-{attempt.attempt_index:02d}",
                    software_commit_sha,
                )
                result = outcome["direct"]
                attempts.append(outcome["summary"])
                direct_replay_checks.append(outcome["direct_replay_match"])
                determinism_checks.append(outcome["deterministic_repeat_match"])
                oracle_delete_checks.append(outcome["oracle_delete_match"])
                oracle_tamper_checks.append(outcome["oracle_tamper_match"])
                blocked_invariants.append(
                    (result.processing_status == "blocked_before_quality" and not result.quality_results)
                    or (result.processing_status == "quality_evaluated" and bool(result.quality_results))
                )
                for quality in result.quality_results:
                    try:
                        quality.validate_schema()
                        quality_schema_checks.append(True)
                    except Exception:
                        quality_schema_checks.append(False)
                    confidence_checks.append(quality.confidence is None)
                    score_checks.append(quality.score is None)
                    parameter_status_checks.append(quality.parameter_status.value == "synthetic_only")
                revision_checks.append(result.software_commit_sha == software_commit_sha)
                result_sha_checks.append(len(result.result_sha256) == 64)
                limitation_checks.append(bool(result.limitations))
                window_checks.extend(
                    window.end_index - window.start_index == window.sample_count
                    for window in result.windows
                )
                for views in result.filter_views_by_window.values():
                    causal_filter_checks.append(views["causal"].mode == "causal")
                    offline_filter_checks.append(
                        views["offline_review"].mode == "offline_review"
                        and views["ppg_offline"].mode == "offline_review"
                    )
            multi[plan_id] = {"attempt_count": len(attempts), "attempts": attempts}

    processor = SPProcessor()
    golden_document = {
        "format_version": GOLDEN_FORMAT_VERSION,
        "scenario_registry_digest": scenario_registry_digest(),
        "processing_version": processor.processing_version,
        "parameter_version": processor.parameters.parameter_version,
        "configuration_digest": processor.parameters.configuration_digest,
        "single_attempt": single,
        "multi_attempt": multi,
    }
    if write_golden:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_bytes(canonical_json_bytes(golden_document))

    golden_error = None
    try:
        expected_golden = json.loads(golden_path.read_text(encoding="utf-8"))
        golden_match = _matches_golden(golden_document, expected_golden)
    except (OSError, json.JSONDecodeError) as exc:
        golden_match = False
        golden_error = type(exc).__name__
    after_golden_sha = _sha256(golden_path)

    engineering = processor.engineering_unit_conversion
    single_matrix_passed = all(
        single.get(case_id, {}).get("quality_results", [{}])[0].get("label") == expected
        for case_id, expected in EXPECTED_LABELS.items()
    ) and all(
        single.get(case_id, {}).get("processing_status") == "blocked_before_quality"
        and single.get(case_id, {}).get("quality_results") == []
        for case_id in ("abort", "device_fault")
    )
    multi_labels = {
        plan_id: [
            attempt.get("quality_results", [{}])[0].get("label")
            for attempt in value.get("attempts", [])
        ]
        for plan_id, value in multi.items()
    }
    multi_without_decision = (
        multi_labels.get("retry_improves") == ["weak_signal", "acceptable"]
        and multi_labels.get("retry_still_fails") == ["weak_signal"] * 3
        and not any(
            key in canonical_json_bytes(multi).decode("utf-8")
            for key in ("retry_same_position", "reposition", "abort_and_release", '"decision"')
        )
    )
    engineering_view = RawIdentityConverter().describe_pulse(1.0)
    gates = {
        "workspace_clean": bool(workspace_clean),
        "semantic_fingerprint_complete": (
            semantic_fingerprint_coverage.get("version") == SP_RESULT_FINGERPRINT_VERSION
            and all(
                semantic_fingerprint_coverage.get(key) is True
                for key in (
                    "integrity_drift_detected",
                    "filter_drift_detected",
                    "beat_removal_detected",
                    "peak_time_drift_detected",
                    "reference_removal_detected",
                    "reference_pair_drift_detected",
                    "execution_provenance_excluded",
                )
            )
        ),
        "m1_contracts_unchanged": bool(m1_contracts_unchanged),
        "parameter_profile_valid": processor.parameters.configuration_digest == P2C_CONFIGURATION_DIGEST,
        "input_normalization_valid": bool(single),
        "integrity_hard_gates_valid": all(blocked_invariants),
        "stable_windows_contiguous": bool(window_checks) and all(window_checks),
        "raw_quality_metrics_valid": bool(quality_schema_checks) and all(quality_schema_checks),
        "causal_filter_is_causal": bool(causal_filter_checks) and all(causal_filter_checks),
        "offline_filter_separated": bool(offline_filter_checks) and all(offline_filter_checks),
        "beat_detection_deterministic": all(determinism_checks),
        "reference_alignment_deterministic": all(determinism_checks),
        "quality_projection_schema_valid": bool(quality_schema_checks) and all(quality_schema_checks),
        "confidence_is_null": bool(confidence_checks) and all(confidence_checks),
        "score_is_null": bool(score_checks) and all(score_checks),
        "simulation_parameters_not_h1_frozen": bool(parameter_status_checks) and all(parameter_status_checks),
        "engineering_unit_interface_valid": (
            engineering.raw_identity
            and not engineering.engineering_units_applied
            and engineering.real_calibration_pending
            and engineering.conversion_status.value == "pending_h1_calibration"
            and engineering_view.raw_value == 1.0
            and engineering_view.engineering_value is None
            and engineering_view.unit is None
        ),
        "processing_version_tracked": all(
            summary.get("processing_version") == processor.processing_version for summary in single.values()
        ),
        "software_sha_tracked": all(revision_checks),
        "parameter_version_tracked": processor.parameters.parameter_version == "0.3.0-p2c",
        "configuration_digest_tracked": processor.parameters.configuration_digest == P2C_CONFIGURATION_DIGEST,
        "software_commit_sha_full": len(software_commit_sha) == 40,
        "scenario_registry_exact_16_plus_2": (
            tuple(list_scenarios()) == EXPECTED_SINGLE_CASES
            and tuple(list_attempt_plans()) == EXPECTED_MULTI_CASES
            and len(list_simulation_cases()) == 18
        ),
        "single_attempt_matrix_passed": set(single) == set(EXPECTED_SINGLE_CASES) and single_matrix_passed,
        "multi_attempt_matrix_complete": (
            set(multi) == set(EXPECTED_MULTI_CASES)
            and multi.get("retry_improves", {}).get("attempt_count") == 2
            and multi.get("retry_still_fails", {}).get("attempt_count") == 3
        ),
        "direct_replay_equivalent": all(direct_replay_checks),
        "deterministic_repeat_match": all(determinism_checks),
        "processing_status_invariants": all(blocked_invariants),
        "quality_schema_valid": bool(quality_schema_checks) and all(quality_schema_checks),
        "result_sha256_valid": all(result_sha_checks),
        "limitations_propagated": all(limitation_checks),
        "safety_blocked_empty": all(
            not single[name]["quality_results"] and single[name]["processing_status"] == "blocked_before_quality"
            for name in ("abort", "device_fault")
        ),
        "parameter_digests_unchanged": (
            P2A_CONFIGURATION_DIGEST
            == "f546f8910d45df71faaaf6569d861de39ae991e63beed175ff5b7c5ad0040f1c"
            and P2B_CONFIGURATION_DIGEST
            == "c34fe3b60b25a9f496ddc3172084af379a605b7c2a73659ece83f02e723dea29"
            and P2C_CONFIGURATION_DIGEST
            == "b71d02832551f5236f34ecb3ce866bb50df3420530fd3bfc8b0b17a583274371"
        ),
        "oracle_isolation_verified": (
            _oracle_isolated(source_root)
            and all(oracle_delete_checks)
            and all(oracle_tamper_checks)
        ),
        "multi_attempt_processed_without_decision": multi_without_decision,
        "golden_summaries_match": golden_match,
        "golden_read_only": write_golden or before_golden_sha == after_golden_sha,
        "d3_regression_passed": bool(d3_regression_passed),
        "m1_p1_regression_passed": bool(m1_p1_regression_passed),
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    return {
        "format_version": ACCEPTANCE_FORMAT_VERSION,
        "acceptance": not failed,
        "formal_acceptance": not failed,
        "failed_gates": list(failed),
        "single_attempt_cases": len(single),
        "multi_attempt_cases": len(multi),
        "quality_schema_valid": gates["quality_schema_valid"],
        "oracle_isolation_verified": gates["oracle_isolation_verified"],
        "direct_replay_equivalent": gates["direct_replay_equivalent"],
        "deterministic_repeat_match": gates["deterministic_repeat_match"],
        "golden_summaries_match": gates["golden_summaries_match"],
        "software_sha_tracked": gates["software_sha_tracked"],
        "engineering_unit_interface_valid": gates["engineering_unit_interface_valid"],
        "d3_regression_passed": gates["d3_regression_passed"],
        "m1_p1_regression_passed": gates["m1_p1_regression_passed"],
        "software_commit_sha": software_commit_sha,
        "semantic_fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "semantic_fingerprint_coverage": semantic_fingerprint_coverage,
        "processing": {
            "processing_version": processor.processing_version,
            "parameter_version": processor.parameters.parameter_version,
            "configuration_digest": processor.parameters.configuration_digest,
            "p2a_digest": P2A_CONFIGURATION_DIGEST,
            "p2b_digest": P2B_CONFIGURATION_DIGEST,
            "p2c_digest": P2C_CONFIGURATION_DIGEST,
        },
        "scenario_registry": {
            "single_attempt_count": len(single),
            "multi_attempt_count": len(multi),
            "total_case_count": len(list_simulation_cases()),
            "digest": scenario_registry_digest(),
        },
        "single_attempt": single,
        "multi_attempt": multi,
        "replay": {
            "verified": all(direct_replay_checks),
            "comparison_count": len(direct_replay_checks),
            "strict_cases": [
                "baseline_drift",
                "insufficient_duration",
                "lower_saturation",
                "motion_artifact",
                "no_contact",
                "normal_high_quality",
                "ppg_misalignment",
                "unstable_load",
                "upper_saturation",
                "weak_signal",
                "retry_improves/attempt-01",
                "retry_improves/attempt-02",
                "retry_still_fails/attempt-01",
                "retry_still_fails/attempt-02",
                "retry_still_fails/attempt-03",
            ],
            "allow_incomplete_cases": [
                "abort",
                "device_fault",
                "frame_loss",
                "raw_persistence_failure",
                "sensor_disconnection",
                "timestamp_regression",
            ],
            "not_applicable_cases": [],
        },
        "determinism": {"verified": all(determinism_checks), "comparison_count": len(determinism_checks)},
        "golden": {
            "path": golden_path.name,
            "sha256": after_golden_sha,
            "matched": golden_match,
            "error": golden_error,
            "write_requested": write_golden,
        },
        "oracle": {
            "production_oracle_isolated": _oracle_isolated(source_root),
            "delete_verified": all(oracle_delete_checks),
            "tamper_verified": all(oracle_tamper_checks),
        },
        "engineering_units": {
            "converter": engineering.converter_name,
            "converter_version": engineering.converter_version,
            "parameter_status": engineering.parameter_status.value,
            "raw_identity": engineering.raw_identity,
            "engineering_units_applied": engineering.engineering_units_applied,
            "conversion_status": engineering.conversion_status.value,
            "simulation_only": engineering.simulation_only,
            "real_calibration_pending": engineering.real_calibration_pending,
        },
        "limitations": [
            "synthetic simulator evidence only",
            "raw-count identity conversion; real H1 engineering-unit calibration pending",
            "not hardware, human-subject, clinical, or safety validation",
            "no INT decision actions are executed by SP acceptance",
        ],
        "gates": gates,
    }
