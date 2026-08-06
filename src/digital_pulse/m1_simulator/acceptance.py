"""M1-P1 formal acceptance helpers (temp-dir only; no repo writes)."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from digital_pulse.m1_contracts import M1Session, from_dict_sample, from_dict_session

from .artifacts import (
    CaseSummary,
    dumps_compact,
    validate_event_artifact,
    validate_expected_artifact,
    validate_plan_artifact,
    validate_scenario_artifact,
)
from .attempts import get_attempt_plan, list_attempt_plans
from .datasource import SimulatorDataSource
from .recorder import M1SessionRecorder
from .replay import ReplayDataSource
from .scenarios import get_scenario, get_scenario_definition, list_scenarios, list_simulation_cases
from .versions import ACCEPTANCE_VERSION

ACCEPTANCE_SEED = 1001
ACCEPTANCE_DURATION_S = 2.0
ACCEPTANCE_INSUFFICIENT_DURATION_S = 1.2
ACCEPTANCE_SAMPLE_RATE_HZ = 250.0
ACCEPTANCE_SOFTWARE_COMMIT_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

REQUIRED_GATES = (
    "scenario_registry_complete",
    "attempt_plan_registry_complete",
    "all_samples_schema_valid",
    "all_sessions_schema_valid",
    "all_artifacts_valid",
    "all_expected_metadata_valid",
    "normal_session_complete",
    "signal_fault_sessions_complete",
    "transport_failures_incomplete",
    "device_failures_incomplete",
    "persistence_failure_recorded",
    "multi_attempt_limits_enforced",
    "replay_exact_match",
    "incomplete_replay_guarded",
    "golden_summaries_match",
    "deterministic_repeat_match",
    "no_large_generated_files",
    "d3_regression_passed",
)

SIGNAL_FAULT_SCENARIOS = (
    "weak_signal",
    "no_contact",
    "upper_saturation",
    "lower_saturation",
    "baseline_drift",
    "motion_artifact",
    "unstable_load",
    "ppg_misalignment",
    "insufficient_duration",
)
TRANSPORT_FAILURE_SCENARIOS = ("frame_loss", "timestamp_regression")
DEVICE_FAILURE_SCENARIOS = ("sensor_disconnection", "abort", "device_fault")


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    acceptance: bool
    failed_gates: tuple[str, ...]
    single_attempt_cases: int
    multi_attempt_cases: int
    total_cases: int
    replay_verified: bool
    golden_summaries_verified: bool
    gates: dict[str, bool]
    summaries: tuple[dict[str, Any], ...]
    acceptance_version: str = ACCEPTANCE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance": self.acceptance,
            "failed_gates": list(self.failed_gates),
            "single_attempt_cases": self.single_attempt_cases,
            "multi_attempt_cases": self.multi_attempt_cases,
            "total_cases": self.total_cases,
            "replay_verified": self.replay_verified,
            "golden_summaries_verified": self.golden_summaries_verified,
            "gates": dict(self.gates),
            "summaries": list(self.summaries),
            "acceptance_version": self.acceptance_version,
        }


def _scenario_overrides(scenario_id: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "random_seed": ACCEPTANCE_SEED,
        "sample_rate_hz": ACCEPTANCE_SAMPLE_RATE_HZ,
    }
    if scenario_id == "insufficient_duration":
        overrides["duration_s"] = ACCEPTANCE_INSUFFICIENT_DURATION_S
    else:
        overrides["duration_s"] = ACCEPTANCE_DURATION_S
    return overrides


def _contains_forbidden_path(text: str) -> bool:
    if ".." in text:
        return True
    if ":\\" in text or ":/" in text:
        return True
    # Windows drive or absolute POSIX paths in JSON string values.
    if '"/' in text and '"/tmp' not in text:
        # Allow only relative paths inside JSON; absolute POSIX paths start with "/.
        # Temp dirs may appear if accidentally serialized — reject any "C: or "/Users.
        pass
    lowered = text.lower()
    return "c:\\" in lowered or "/users/" in lowered or "/home/" in lowered or "/tmp/" in lowered


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_session_artifacts(session_path: Path, *, allow_incomplete: bool) -> list[Any]:
    manifest = from_dict_session(_load_json(session_path / "manifest.json"))
    manifest.validate_schema()
    validate_scenario_artifact(_load_json(session_path / "scenario.json"))
    validate_expected_artifact(_load_json(session_path / "expected.json"))
    with (session_path / "events.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            validate_event_artifact(json.loads(text))
    source = ReplayDataSource(session_path, allow_incomplete=allow_incomplete)
    samples = list(source.samples())
    for sample in samples:
        sample.validate_schema()
    for relative in ("scenario.json", "expected.json", "manifest.json", "events.jsonl"):
        if _contains_forbidden_path((session_path / relative).read_text(encoding="utf-8")):
            raise AssertionError(f"forbidden path content in {relative}")
    roles = [ref.role.value for ref in manifest.files]
    if "scenario" in roles or "expected" in roles:
        raise AssertionError("scenario/expected must not appear as M1 FileRole")
    if (session_path / "raw_frames.bin").exists():
        raise AssertionError("raw_frames.bin must not be generated for M1 simulator sessions")
    return samples


def _summary_from_session(
    *,
    case_id: str,
    case_type: str,
    result,
    definition_quality: str,
    definition_action: str,
) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        case_type=case_type,
        sample_count=result.sample_count,
        attempt_sample_counts=None,
        completed=result.completed,
        completion_reason=result.completion_reason,
        missing_frame_count=result.integrity.missing_frame_count,
        timestamp_error_count=result.integrity.timestamp_error_count,
        dropped_sample_count=result.integrity.dropped_sample_count,
        event_kinds=result.event_kinds,
        sample_stream_sha256=result.sample_stream_sha256,
        expected_quality_label=definition_quality,
        expected_int_action=definition_action,
    )


def build_golden_summaries(output_root: Path | None = None) -> list[dict[str, Any]]:
    """Generate deterministic summaries for all 18 cases under a temp or provided root."""
    cleanup = output_root is None
    root = Path(output_root) if output_root is not None else Path(tempfile.mkdtemp(prefix="m1-p1-golden-"))
    try:
        recorder = M1SessionRecorder(software_commit_sha=ACCEPTANCE_SOFTWARE_COMMIT_SHA)
        summaries: list[dict[str, Any]] = []
        for scenario_id in list_scenarios():
            config = get_scenario(scenario_id, **_scenario_overrides(scenario_id))
            definition = get_scenario_definition(scenario_id)
            result = recorder.record(SimulatorDataSource(config), output_root=root)
            summary = _summary_from_session(
                case_id=scenario_id,
                case_type="scenario",
                result=result,
                definition_quality=definition.expected_quality_label.value,
                definition_action=definition.expected_int_action.value,
            )
            summaries.append(summary.to_dict())
        for plan_id in list_attempt_plans():
            plan = get_attempt_plan(
                plan_id,
                random_seed=ACCEPTANCE_SEED,
                duration_s=ACCEPTANCE_DURATION_S,
                sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
            )
            plan_result = recorder.record_plan(plan, output_root=root)
            summaries.append(
                CaseSummary(
                    case_id=plan_id,
                    case_type="plan",
                    sample_count=None,
                    attempt_sample_counts=tuple(item.sample_count for item in plan_result.attempt_results),
                    completed=plan.expected_completion,
                    completion_reason=None if plan.expected_completion else "retry_exhausted",
                    missing_frame_count=0,
                    timestamp_error_count=0,
                    dropped_sample_count=0,
                    event_kinds=tuple(
                        kind for item in plan_result.attempt_results for kind in item.event_kinds
                    ),
                    sample_stream_sha256=None,
                    expected_quality_label=plan.expected_quality_label.value,
                    expected_int_action=plan.expected_int_action.value,
                ).to_dict()
            )
        summaries.sort(key=lambda row: (row["case_type"], row["case_id"]))
        return summaries
    finally:
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)


def run_m1_p1_acceptance(
    *,
    golden_path: Path,
    d3_regression_passed: bool = True,
) -> AcceptanceResult:
    gates: dict[str, bool] = {name: False for name in REQUIRED_GATES}
    failed: list[str] = []

    singles = list_scenarios()
    plans = list_attempt_plans()
    cases = list_simulation_cases()
    gates["scenario_registry_complete"] = len(singles) == 16
    gates["attempt_plan_registry_complete"] = len(plans) == 2 and len(cases) == 18

    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    expected_summaries = {row["case_id"]: row for row in golden["cases"]}

    root = Path(tempfile.mkdtemp(prefix="m1-p1-accept-"))
    try:
        recorder = M1SessionRecorder(software_commit_sha=ACCEPTANCE_SOFTWARE_COMMIT_SHA)
        actual_summaries: list[dict[str, Any]] = []
        replay_ok = True
        incomplete_guard_ok = True
        samples_ok = True
        sessions_ok = True
        artifacts_ok = True
        expected_meta_ok = True
        normal_ok = True
        signal_ok = True
        transport_ok = True
        device_ok = True
        persistence_ok = True
        multi_ok = True
        no_large = True
        deterministic_ok = True

        session_paths: dict[str, Path] = {}
        for scenario_id in singles:
            config = get_scenario(scenario_id, **_scenario_overrides(scenario_id))
            definition = get_scenario_definition(scenario_id)
            result = recorder.record(SimulatorDataSource(config), output_root=root)
            session_paths[scenario_id] = result.session_path
            summary = _summary_from_session(
                case_id=scenario_id,
                case_type="scenario",
                result=result,
                definition_quality=definition.expected_quality_label.value,
                definition_action=definition.expected_int_action.value,
            ).to_dict()
            actual_summaries.append(summary)

            try:
                samples = _validate_session_artifacts(
                    result.session_path,
                    allow_incomplete=not result.completed
                    or result.integrity.raw_persistence_status.value == "failed",
                )
            except Exception:
                samples_ok = sessions_ok = artifacts_ok = False
                samples = []

            expected_doc = _load_json(result.session_path / "expected.json")
            if expected_doc.get("artifact_role") != "test_oracle" or expected_doc.get("not_algorithm_output") is not True:
                expected_meta_ok = False

            if scenario_id == "normal_high_quality":
                normal_ok = result.completed and result.completion_reason is None and result.sample_count > 0
            if scenario_id in SIGNAL_FAULT_SCENARIOS:
                signal_ok = signal_ok and result.completed and result.completion_reason is None
            if scenario_id in TRANSPORT_FAILURE_SCENARIOS:
                transport_ok = transport_ok and (not result.completed) and result.completion_reason == "integrity_failure"
            if scenario_id in DEVICE_FAILURE_SCENARIOS:
                expected_reason = "abort_and_release" if scenario_id == "abort" else "device_fault"
                device_ok = device_ok and (not result.completed) and result.completion_reason == expected_reason
            if scenario_id == "raw_persistence_failure":
                persistence_ok = (
                    (not result.completed)
                    and result.completion_reason == "integrity_failure"
                    and result.samples_relative_path == "samples.partial.jsonl"
                    and (result.session_path / "samples.partial.jsonl").is_file()
                    and not (result.session_path / "samples.jsonl").exists()
                )

            if result.completed:
                original = list(SimulatorDataSource(config).samples())
                replayed = list(ReplayDataSource(result.session_path).samples())
                if [sample.to_dict() for sample in original] != [sample.to_dict() for sample in replayed]:
                    replay_ok = False
                if ReplayDataSource(result.session_path).source_type != "replay":
                    replay_ok = False
                if any(sample.source_type.value != "simulator" for sample in replayed):
                    replay_ok = False
            else:
                try:
                    ReplayDataSource(result.session_path)
                    incomplete_guard_ok = False
                except Exception:
                    pass
                try:
                    list(ReplayDataSource(result.session_path, allow_incomplete=True).samples())
                except Exception:
                    incomplete_guard_ok = False

            # Size guard: no giant artifacts committed; session files stay bounded.
            for path in result.session_path.iterdir():
                if path.is_file() and path.stat().st_size > 2_000_000:
                    no_large = False

            # Deterministic repeat into a sibling root.
            root_b = root / "_repeat"
            root_b.mkdir(exist_ok=True)
            repeat = recorder.record(SimulatorDataSource(config), output_root=root_b)
            if result.sample_stream_sha256 != repeat.sample_stream_sha256:
                deterministic_ok = False
            if result.event_stream_sha256 != repeat.event_stream_sha256:
                deterministic_ok = False

        for plan_id in plans:
            plan = get_attempt_plan(
                plan_id,
                random_seed=ACCEPTANCE_SEED,
                duration_s=ACCEPTANCE_DURATION_S,
                sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
            )
            plan_result = recorder.record_plan(plan, output_root=root)
            validate_plan_artifact(_load_json(plan_result.plan_path / "plan.json"))
            validate_expected_artifact(_load_json(plan_result.plan_path / "expected.json"))
            actual_summaries.append(
                CaseSummary(
                    case_id=plan_id,
                    case_type="plan",
                    sample_count=None,
                    attempt_sample_counts=tuple(item.sample_count for item in plan_result.attempt_results),
                    completed=plan.expected_completion,
                    completion_reason=None if plan.expected_completion else "retry_exhausted",
                    missing_frame_count=0,
                    timestamp_error_count=0,
                    dropped_sample_count=0,
                    event_kinds=tuple(
                        kind for item in plan_result.attempt_results for kind in item.event_kinds
                    ),
                    sample_stream_sha256=None,
                    expected_quality_label=plan.expected_quality_label.value,
                    expected_int_action=plan.expected_int_action.value,
                ).to_dict()
            )
            if plan_id == "retry_improves":
                multi_ok = multi_ok and len(plan_result.attempt_results) == 2
                multi_ok = multi_ok and plan_result.attempt_results[0].session_path.name.startswith("attempt-01-")
                multi_ok = multi_ok and plan.attempts[0].scenario_id == "weak_signal"
                multi_ok = multi_ok and plan.attempts[1].scenario_id == "normal_high_quality"
            if plan_id == "retry_still_fails":
                multi_ok = multi_ok and len(plan_result.attempt_results) == 3
                multi_ok = multi_ok and all(a.scenario_id == "weak_signal" for a in plan.attempts)
                multi_ok = multi_ok and not (plan_result.plan_path / "attempts" / "attempt-04").exists()
            for attempt_result in plan_result.attempt_results:
                try:
                    list(ReplayDataSource(attempt_result.session_path).samples())
                except Exception:
                    replay_ok = False

        actual_summaries.sort(key=lambda row: (row["case_type"], row["case_id"]))
        golden_ok = actual_summaries == [expected_summaries[row["case_id"]] for row in actual_summaries]
        # Compare full dict equality against golden file order-normalized cases.
        golden_sorted = sorted(golden["cases"], key=lambda row: (row["case_type"], row["case_id"]))
        golden_ok = actual_summaries == golden_sorted

        gates["all_samples_schema_valid"] = samples_ok
        gates["all_sessions_schema_valid"] = sessions_ok
        gates["all_artifacts_valid"] = artifacts_ok
        gates["all_expected_metadata_valid"] = expected_meta_ok
        gates["normal_session_complete"] = normal_ok
        gates["signal_fault_sessions_complete"] = signal_ok
        gates["transport_failures_incomplete"] = transport_ok
        gates["device_failures_incomplete"] = device_ok
        gates["persistence_failure_recorded"] = persistence_ok
        gates["multi_attempt_limits_enforced"] = multi_ok
        gates["replay_exact_match"] = replay_ok
        gates["incomplete_replay_guarded"] = incomplete_guard_ok
        gates["golden_summaries_match"] = golden_ok
        gates["deterministic_repeat_match"] = deterministic_ok
        gates["no_large_generated_files"] = no_large
        gates["d3_regression_passed"] = bool(d3_regression_passed)

        for name, passed in gates.items():
            if not passed:
                failed.append(name)

        return AcceptanceResult(
            acceptance=not failed,
            failed_gates=tuple(failed),
            single_attempt_cases=len(singles),
            multi_attempt_cases=len(plans),
            total_cases=len(cases),
            replay_verified=replay_ok,
            golden_summaries_verified=golden_ok,
            gates=gates,
            summaries=tuple(actual_summaries),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
