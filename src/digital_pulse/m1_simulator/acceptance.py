"""M1-P1 formal acceptance helpers (temp-dir only; no repo writes)."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from digital_pulse.m1_contracts import FileRole, from_dict_session

from .artifacts import (
    CaseSummary,
    session_completion_for,
    validate_event_artifact,
    validate_expected_artifact,
    validate_plan_artifact,
    validate_scenario_artifact,
)
from .attempts import get_attempt_plan, list_attempt_plans
from .datasource import SimulatorDataSource
from .paths import is_forbidden_relative_path
from .recorder import M1SessionRecorder
from .replay import ReplayDataSource, resolve_file_role
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

_ATTEMPT_DIR_RE = re.compile(r"^attempt-(\d{2})-(.+)$")


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
    d3_regression_passed: bool | None = True
    d3_regression_skipped: bool = False
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
            "d3_regression_passed": self.d3_regression_passed,
            "d3_regression_skipped": self.d3_regression_skipped,
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


def parse_attempt_directory_name(name: str) -> tuple[int, str]:
    match = _ATTEMPT_DIR_RE.fullmatch(name)
    if not match:
        raise ValueError(f"invalid attempt directory name: {name}")
    return int(match.group(1)), match.group(2)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(session_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with (session_path / "events.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            validate_event_artifact(row)
            rows.append(row)
    return rows


def _assert_no_absolute_paths_in_artifact(path: Path) -> None:
    payload = _load_json(path) if path.suffix == ".json" else None
    if path.suffix == ".json":
        _walk_path_fields(payload, path.name)
        text = path.read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.strip():
                continue
            _walk_path_fields(json.loads(line), path.name)
    # Text-level leak detection for accidental absolute roots in any string value.
    lowered = text.lower()
    for marker in ("c:\\", "c:/", "/users/", "/home/", "/tmp/", "/var/", "/private/"):
        if marker in lowered:
            raise AssertionError(f"absolute path leak in {path.name}: {marker}")


def _walk_path_fields(node: Any, context: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"relative_path", "output_path", "path"} and isinstance(value, str):
                if is_forbidden_relative_path(value):
                    raise AssertionError(f"forbidden path field {key} in {context}: {value!r}")
            else:
                _walk_path_fields(value, context)
    elif isinstance(node, list):
        for item in node:
            _walk_path_fields(item, context)


def _validate_session_artifacts(session_path: Path, *, allow_incomplete: bool) -> list[Any]:
    manifest = from_dict_session(_load_json(session_path / "manifest.json"))
    manifest.validate_schema()
    validate_scenario_artifact(_load_json(session_path / "scenario.json"))
    validate_expected_artifact(_load_json(session_path / "expected.json"))
    _load_events(session_path)
    for relative in ("manifest.json", "scenario.json", "expected.json", "events.jsonl"):
        _assert_no_absolute_paths_in_artifact(session_path / relative)
    source = ReplayDataSource(session_path, allow_incomplete=allow_incomplete)
    samples = list(source.samples())
    for sample in samples:
        sample.validate_schema()
    roles = [ref.role.value for ref in manifest.files]
    if "scenario" in roles or "expected" in roles:
        raise AssertionError("scenario/expected must not appear as M1 FileRole")
    if (session_path / "raw_frames.bin").exists():
        raise AssertionError("raw_frames.bin must not be generated for M1 simulator sessions")
    resolve_file_role(session_path, manifest, FileRole.SAMPLES)
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


def _observe_transport(session_path: Path, scenario_id: str, samples: list[Any]) -> bool:
    expected_completed, expected_reason = session_completion_for(scenario_id)
    manifest = from_dict_session(_load_json(session_path / "manifest.json"))
    events = _load_events(session_path)
    kinds = {row["kind"] for row in events}
    if manifest.completed != expected_completed or manifest.completion_reason != expected_reason:
        return False
    if expected_reason != "integrity_failure":
        return False
    integrity = manifest.integrity_summary
    if scenario_id == "frame_loss":
        if "frame_loss" not in kinds:
            return False
        if integrity.missing_frame_count <= 0 or integrity.dropped_sample_count <= 0:
            return False
        if not any(not sample.receive_integrity.sequence_valid for sample in samples):
            return False
    if scenario_id == "timestamp_regression":
        if "timestamp_regression" not in kinds:
            return False
        if integrity.timestamp_error_count <= 0:
            return False
        if not any(not sample.receive_integrity.timestamp_valid for sample in samples):
            return False
    return True


def _observe_device(session_path: Path, scenario_id: str, samples: list[Any]) -> bool:
    expected_completed, expected_reason = session_completion_for(scenario_id)
    manifest = from_dict_session(_load_json(session_path / "manifest.json"))
    events = _load_events(session_path)
    kinds = {row["kind"] for row in events}
    if not samples:
        return False
    if manifest.completed != expected_completed or manifest.completion_reason != expected_reason:
        return False
    terminal = samples[-1]
    flags = set(terminal.fault_flags)
    if scenario_id == "sensor_disconnection":
        return (
            terminal.device_state == "FAULT"
            and terminal.pulse.status.value == "disconnected"
            and terminal.pulse.value is None
            and "sensor_disconnection" in kinds
            and "sensor_disconnected" in flags
        )
    if scenario_id == "device_fault":
        return terminal.device_state == "FAULT" and "device_fault" in kinds and bool(flags)
    if scenario_id == "abort":
        return (
            terminal.device_state == "SAFE_HOLD"
            and "abort" in kinds
            and "emergency_stop" in flags
            and expected_reason == "abort_and_release"
        )
    return False


def _observe_persistence(session_path: Path, result) -> bool:
    expected_completed, expected_reason = session_completion_for("raw_persistence_failure")
    manifest = from_dict_session(_load_json(session_path / "manifest.json"))
    events = _load_events(session_path)
    kinds = {row["kind"] for row in events}
    samples_ref = next(ref for ref in manifest.files if ref.role.value == "samples")
    return (
        manifest.completed is False
        and manifest.completion_reason == expected_reason == "integrity_failure"
        and manifest.integrity_summary.raw_persistence_status.value == "failed"
        and result.samples_relative_path == "samples.partial.jsonl"
        and samples_ref.relative_path == "samples.partial.jsonl"
        and (session_path / "samples.partial.jsonl").is_file()
        and not (session_path / "samples.jsonl").exists()
        and "persistence_failure" in kinds
        and expected_completed is False
    )


def _observe_attempt_limits(plan_path: Path, *, plan_id: str, max_attempts: int) -> bool:
    attempts_root = plan_path / "attempts"
    if not attempts_root.is_dir():
        return False
    dirs = sorted(path for path in attempts_root.iterdir() if path.is_dir())
    indexes: list[int] = []
    for path in dirs:
        try:
            index, _session = parse_attempt_directory_name(path.name)
        except ValueError:
            return False
        indexes.append(index)
    if len(indexes) != max_attempts:
        return False
    if sorted(indexes) != list(range(1, max_attempts + 1)):
        return False
    if any(index > max_attempts for index in indexes):
        return False
    plan_doc = _load_json(plan_path / "plan.json")
    if plan_doc.get("attempt_count") != max_attempts:
        return False
    if plan_id == "retry_improves":
        return [entry["scenario_id"] for entry in plan_doc["attempts"]] == [
            "weak_signal",
            "normal_high_quality",
        ]
    if plan_id == "retry_still_fails":
        return all(entry["scenario_id"] == "weak_signal" for entry in plan_doc["attempts"])
    return True


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
            summaries.append(
                _summary_from_session(
                    case_id=scenario_id,
                    case_type="scenario",
                    result=result,
                    definition_quality=definition.expected_quality_label.value,
                    definition_action=definition.expected_int_action.value,
                ).to_dict()
            )
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
    d3_regression_passed: bool | None = True,
    d3_regression_skipped: bool = False,
) -> AcceptanceResult:
    gates: dict[str, bool] = {name: False for name in REQUIRED_GATES}
    failed: list[str] = []

    singles = list_scenarios()
    plans = list_attempt_plans()
    cases = list_simulation_cases()
    gates["scenario_registry_complete"] = len(singles) == 16
    gates["attempt_plan_registry_complete"] = len(plans) == 2 and len(cases) == 18

    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))

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

        for scenario_id in singles:
            config = get_scenario(scenario_id, **_scenario_overrides(scenario_id))
            definition = get_scenario_definition(scenario_id)
            result = recorder.record(SimulatorDataSource(config), output_root=root)
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
                    allow_incomplete=(
                        not result.completed
                        or result.integrity.raw_persistence_status.value == "failed"
                    ),
                )
            except Exception:
                samples_ok = sessions_ok = artifacts_ok = False
                samples = []

            expected_doc = _load_json(result.session_path / "expected.json")
            if expected_doc.get("artifact_role") != "test_oracle" or expected_doc.get("not_algorithm_output") is not True:
                expected_meta_ok = False

            if scenario_id == "normal_high_quality":
                normal_ok = (
                    result.completed
                    and result.completion_reason is None
                    and result.sample_count > 0
                    and from_dict_session(_load_json(result.session_path / "manifest.json")).completed is True
                )
            if scenario_id in SIGNAL_FAULT_SCENARIOS:
                manifest = from_dict_session(_load_json(result.session_path / "manifest.json"))
                expected_completed, expected_reason = session_completion_for(scenario_id)
                signal_ok = signal_ok and (
                    manifest.completed is True
                    and manifest.completion_reason is None
                    and expected_completed is True
                    and expected_reason is None
                )
            if scenario_id in TRANSPORT_FAILURE_SCENARIOS:
                transport_ok = transport_ok and _observe_transport(result.session_path, scenario_id, samples)
            if scenario_id in DEVICE_FAILURE_SCENARIOS:
                device_ok = device_ok and _observe_device(result.session_path, scenario_id, samples)
            if scenario_id == "raw_persistence_failure":
                persistence_ok = _observe_persistence(result.session_path, result)

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

            for path in result.session_path.iterdir():
                if path.is_file() and path.stat().st_size > 2_000_000:
                    no_large = False

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
            plan_doc = _load_json(plan_result.plan_path / "plan.json")
            validate_plan_artifact(plan_doc)
            validate_expected_artifact(_load_json(plan_result.plan_path / "expected.json"))
            _assert_no_absolute_paths_in_artifact(plan_result.plan_path / "plan.json")
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
            multi_ok = multi_ok and _observe_attempt_limits(
                plan_result.plan_path,
                plan_id=plan_id,
                max_attempts=plan.max_attempts,
            )
            # Observed attempt fields must match plan.json aggregation from disk results.
            for entry, result in zip(plan_doc["attempts"], plan_result.attempt_results, strict=True):
                multi_ok = multi_ok and entry["session_id"] == result.session_id
                multi_ok = multi_ok and entry["sample_count"] == result.sample_count
                multi_ok = multi_ok and entry["completed"] == result.completed
                multi_ok = multi_ok and entry["completion_reason"] == result.completion_reason
            for attempt_result in plan_result.attempt_results:
                try:
                    list(ReplayDataSource(attempt_result.session_path).samples())
                except Exception:
                    replay_ok = False

        actual_summaries.sort(key=lambda row: (row["case_type"], row["case_id"]))
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
        if d3_regression_skipped:
            gates["d3_regression_passed"] = True
        else:
            gates["d3_regression_passed"] = d3_regression_passed is True

        for name, passed in gates.items():
            if name == "d3_regression_passed" and d3_regression_skipped:
                continue
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
            d3_regression_passed=d3_regression_passed,
            d3_regression_skipped=d3_regression_skipped,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
