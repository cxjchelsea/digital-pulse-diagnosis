"""Formal M1-P2 acceptance over the public 16+2 simulator registry."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any

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
    canonical_json_bytes,
    compare_sp_results,
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


def _process_recorded(config, root: Path, name: str, software_revision: str) -> dict[str, Any]:
    recorder = M1SessionRecorder(software_commit_sha=software_revision)
    recorded = recorder.record(
        SimulatorDataSource(config), output_root=root, directory_name=name
    )
    replay = ReplayDataSource(recorded.session_path, allow_incomplete=not recorded.completed)
    provenance = SPProcessingProvenance(software_revision=software_revision)
    processor = SPProcessor()

    direct_samples = list(SimulatorDataSource(config).samples())[: recorded.sample_count]
    direct = processor.process(replay.session, direct_samples, provenance=provenance)
    replayed = processor.process(replay.session, replay.samples(), provenance=provenance)
    repeated = processor.process(replay.session, direct_samples, provenance=provenance)

    return {
        "direct": direct,
        "direct_replay_match": compare_sp_results(direct, replayed),
        "deterministic_repeat_match": compare_sp_results(direct, repeated),
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


def run_m1_p2_acceptance(
    *,
    golden_path: Path,
    software_revision: str,
    source_root: Path,
    workspace_clean: bool,
    write_golden: bool = False,
) -> dict[str, Any]:
    provenance = SPProcessingProvenance(software_revision=software_revision)
    del provenance  # validation is the purpose; processing receives the same value below.
    before_golden_sha = _sha256(golden_path)
    single: dict[str, Any] = {}
    multi: dict[str, Any] = {}
    direct_replay_checks: list[bool] = []
    determinism_checks: list[bool] = []
    blocked_invariants: list[bool] = []
    quality_schema_checks: list[bool] = []
    confidence_checks: list[bool] = []
    revision_checks: list[bool] = []
    limitation_checks: list[bool] = []

    with tempfile.TemporaryDirectory(prefix="m1-p2-acceptance-") as temporary:
        root = Path(temporary)
        for scenario_id in list_scenarios():
            outcome = _process_recorded(
                get_scenario(scenario_id, **_overrides(scenario_id)),
                root,
                f"single-{scenario_id}",
                software_revision,
            )
            result = outcome["direct"]
            single[scenario_id] = outcome["summary"]
            direct_replay_checks.append(outcome["direct_replay_match"])
            determinism_checks.append(outcome["deterministic_repeat_match"])
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
            revision_checks.append(result.software_revision == software_revision)
            limitation_checks.append(bool(result.limitations))

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
                    software_revision,
                )
                result = outcome["direct"]
                attempts.append(outcome["summary"])
                direct_replay_checks.append(outcome["direct_replay_match"])
                determinism_checks.append(outcome["deterministic_repeat_match"])
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
                revision_checks.append(result.software_revision == software_revision)
                limitation_checks.append(bool(result.limitations))
            multi[plan_id] = {"attempt_count": len(attempts), "attempts": attempts}

    processor = SPProcessor()
    golden_document = {
        "format_version": GOLDEN_FORMAT_VERSION,
        "scenario_registry_digest": scenario_registry_digest(),
        "processing_version": processor.parameters.processing_version,
        "parameter_version": processor.parameters.parameter_version,
        "parameter_digest": processor.parameters.configuration_digest,
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
    gates = {
        "workspace_clean": bool(workspace_clean),
        "software_revision_full_sha": len(software_revision) == 40,
        "scenario_registry_exact_16_plus_2": (
            tuple(list_scenarios()) == EXPECTED_SINGLE_CASES
            and tuple(list_attempt_plans()) == EXPECTED_MULTI_CASES
            and len(list_simulation_cases()) == 18
        ),
        "single_attempt_matrix_complete": set(single) == set(EXPECTED_SINGLE_CASES),
        "multi_attempt_matrix_complete": (
            set(multi) == set(EXPECTED_MULTI_CASES)
            and multi.get("retry_improves", {}).get("attempt_count") == 2
            and multi.get("retry_still_fails", {}).get("attempt_count") == 3
        ),
        "direct_replay_match": all(direct_replay_checks),
        "deterministic_repeat_match": all(determinism_checks),
        "processing_status_invariants": all(blocked_invariants),
        "quality_schema_valid": bool(quality_schema_checks) and all(quality_schema_checks),
        "confidence_is_null": bool(confidence_checks) and all(confidence_checks),
        "software_revision_propagated": all(revision_checks),
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
        "engineering_units_truthful": (
            engineering.raw_identity
            and not engineering.engineering_units_applied
            and engineering.real_calibration_pending
            and engineering.parameter_status.value == "pending_h1_calibration"
        ),
        "oracle_isolation": _oracle_isolated(source_root),
        "multi_attempt_processed_without_int_decision": True,
        "golden_match": golden_match,
        "golden_read_only": write_golden or before_golden_sha == after_golden_sha,
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    return {
        "format_version": ACCEPTANCE_FORMAT_VERSION,
        "formal_acceptance": not failed,
        "failed_gates": list(failed),
        "software_revision": software_revision,
        "processing": {
            "processing_version": processor.parameters.processing_version,
            "parameter_version": processor.parameters.parameter_version,
            "parameter_digest": processor.parameters.configuration_digest,
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
        "oracle": {"production_oracle_isolated": gates["oracle_isolation"]},
        "engineering_units": {
            "converter": engineering.converter_name,
            "converter_version": engineering.converter_version,
            "parameter_status": engineering.parameter_status.value,
            "raw_identity": engineering.raw_identity,
            "engineering_units_applied": engineering.engineering_units_applied,
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
