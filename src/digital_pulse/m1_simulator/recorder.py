"""M1SessionRecorder: write formal session directories without mutating sample values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from digital_pulse.m1_contracts import (
    FileRole,
    IntegritySummary,
    LimitationCode,
    M1Sample,
    M1Session,
    ParameterStatus,
    RawPersistenceStatus,
    SessionFileRef,
    SourceType,
    VersionManifest,
    to_canonical_dict,
)

from .artifacts import (
    ArtifactError,
    build_expected_artifact,
    build_plan_expected_artifact,
    build_scenario_artifact,
    compute_integrity,
    dumps_compact,
    event_to_artifact_row,
    session_completion_for,
    sha256_file,
    validate_expected_artifact,
    validate_event_artifact,
    validate_plan_artifact,
    validate_scenario_artifact,
    write_text_atomic,
)
from .attempts import MultiAttemptPlan
from .capture import PersistenceWriteError
from .datasource import SimulatorDataSource
from .events import EventRecorder, SimulationEvent
from .paths import safe_child_path, validate_artifact_identifier
from .scenarios import ScenarioDefinition, get_scenario_definition
from .versions import ARTIFACT_FORMAT_VERSION, RECORDER_VERSION


@dataclass(frozen=True, slots=True)
class SessionRecordResult:
    session_id: str
    session_path: Path
    completed: bool
    completion_reason: str | None
    sample_count: int
    samples_relative_path: str
    events_relative_path: str
    configuration_digest: str
    sample_stream_sha256: str | None
    event_stream_sha256: str
    integrity: IntegritySummary
    event_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanRecordResult:
    plan_id: str
    plan_path: Path
    attempt_results: tuple[SessionRecordResult, ...]
    expected_completion: bool


class M1SessionRecorder:
    """Persist SimulatorDataSource output as an M1 session directory."""

    def __init__(self, *, software_commit_sha: str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
        self._software_commit_sha = software_commit_sha

    def record(
        self,
        source: SimulatorDataSource,
        *,
        output_root: Path,
        definition: ScenarioDefinition | None = None,
        session_id: str | None = None,
        directory_name: str | None = None,
    ) -> SessionRecordResult:
        config = source.config
        definition = definition or get_scenario_definition(config.scenario_id)
        # Manifest/sample session_id stays equal to the datasource session_id unless
        # the caller also constructed the source with the same explicit session_id.
        sid = validate_artifact_identifier(session_id or source.session_id, name="session_id")
        dir_name = validate_artifact_identifier(directory_name or sid, name="directory_name")
        session_dir = safe_child_path(Path(output_root), dir_name, name="directory_name")
        if session_dir.exists():
            raise ArtifactError("session_exists", f"session directory already exists: {session_dir}")
        session_dir.mkdir(parents=True, exist_ok=False)

        scenario_doc = build_scenario_artifact(definition, config)
        validate_scenario_artifact(scenario_doc)
        write_text_atomic(session_dir / "scenario.json", dumps_compact(scenario_doc) + "\n")

        expected_doc = build_expected_artifact(definition)
        validate_expected_artifact(expected_doc)
        write_text_atomic(session_dir / "expected.json", dumps_compact(expected_doc) + "\n")

        partial_path = session_dir / "samples.partial.jsonl"
        events_path = session_dir / "events.jsonl"
        persisted: list[M1Sample] = []
        persistence_failed = False
        fail_after = (
            config.persistence_fault_plan.fail_after_persisted_count
            if config.persistence_fault_plan is not None
            else None
        )
        event_recorder = EventRecorder()
        event_recorder.emit("session_started", session_id=sid, scenario_id=config.scenario_id)

        sample_iter = iter(source.samples())
        try:
            with partial_path.open("w", encoding="utf-8", newline="\n") as sample_fh:
                try:
                    for sample in sample_iter:
                        if fail_after is not None and len(persisted) >= fail_after:
                            raise PersistenceWriteError(
                                "raw_persistence_failure",
                                "injected raw persistence failure",
                            )
                        sample.validate_schema()
                        sample_fh.write(dumps_compact(to_canonical_dict(sample)) + "\n")
                        sample_fh.flush()
                        persisted.append(sample)
                except PersistenceWriteError as exc:
                    persistence_failed = True
                    event_recorder.emit(
                        "persistence_failure",
                        failure_code=exc.code,
                        persisted_sample_count=len(persisted),
                    )
        finally:
            close = getattr(sample_iter, "close", None)
            if close is not None:
                close()

        # Merge datasource runtime events after samples() exhausts or is closed.
        runtime_events = list(source.events())
        for event in runtime_events:
            event_recorder.emit(
                event.kind,
                frame_sequence=event.frame_sequence,
                device_time_us=event.device_time_us,
                **{key: value for key, value in event.payload},
            )

        completed, completion_reason = session_completion_for(config.scenario_id)
        if persistence_failed:
            completed = False
            completion_reason = "integrity_failure"
            event_recorder.emit("session_failed", completion_reason=completion_reason)
        elif completed:
            event_recorder.emit("session_completed")
        else:
            event_recorder.emit("session_failed", completion_reason=completion_reason or "other")

        events = event_recorder.events()
        self._write_events(events_path, events)

        runtime = source.runtime_stats()
        dropped = int(runtime.transport_dropped_samples)
        status = RawPersistenceStatus.FAILED if persistence_failed else RawPersistenceStatus.OK
        integrity = compute_integrity(
            persisted,
            dropped_sample_count=dropped,
            raw_persistence_status=status,
            initial_frame_sequence=config.initial_frame_sequence,
        )

        samples_name = "samples.partial.jsonl" if persistence_failed else "samples.jsonl"
        if not persistence_failed:
            final_samples = session_dir / "samples.jsonl"
            partial_path.replace(final_samples)
            sample_sha = sha256_file(final_samples)
        else:
            sample_sha = sha256_file(partial_path) if partial_path.exists() else None

        ended_at = persisted[-1].host_received_at_utc if persisted else config.started_at_utc
        files = (
            SessionFileRef(FileRole.MANIFEST, "manifest.json"),
            SessionFileRef(FileRole.SAMPLES, samples_name),
            SessionFileRef(FileRole.EVENTS, "events.jsonl"),
        )
        session = M1Session(
            session_id=sid,
            source_type=SourceType.SIMULATOR,
            started_at_utc=config.started_at_utc,
            ended_at_utc=ended_at,
            completed=completed,
            completion_reason=completion_reason,
            sample_rate_hz=config.sample_rate_hz,
            configured_channels=("pulse", "load", "ppg"),
            versions=VersionManifest(
                calibration_version=config.parameter_status.value
                if hasattr(config.parameter_status, "value")
                else str(config.parameter_status),
                signal_processing_version=None,
                decision_rule_version=None,
                software_commit_sha=self._software_commit_sha,
                configuration_digest=config.configuration_digest(),
            ),
            integrity_summary=integrity,
            files=files,
            parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
            device_id=None,
            hardware_version=None,
            firmware_version=config.simulator_version,
            protocol_version=0,
            simulator_version=config.simulator_version,
            scenario_id=config.scenario_id,
            random_seed=config.random_seed,
            operator_id=None,
            subject_id=None,
            probe_id=None,
            limitations=(
                LimitationCode.SYNTHETIC_INPUT,
                LimitationCode.PENDING_H1_CALIBRATION,
                LimitationCode.NOT_HARDWARE_VALIDATED,
                LimitationCode.NOT_FOR_MEDICAL_USE,
            ),
        )
        session.validate_schema()
        write_text_atomic(session_dir / "manifest.json", dumps_compact(to_canonical_dict(session)) + "\n")

        return SessionRecordResult(
            session_id=sid,
            session_path=session_dir,
            completed=completed,
            completion_reason=completion_reason,
            sample_count=len(persisted),
            samples_relative_path=samples_name,
            events_relative_path="events.jsonl",
            configuration_digest=config.configuration_digest(),
            sample_stream_sha256=sample_sha,
            event_stream_sha256=sha256_file(events_path),
            integrity=integrity,
            event_kinds=tuple(event.kind for event in events),
        )

    def record_plan(
        self,
        plan: MultiAttemptPlan,
        *,
        output_root: Path,
    ) -> PlanRecordResult:
        plan_id = validate_artifact_identifier(plan.plan_id, name="plan_id")
        plan_dir = safe_child_path(Path(output_root), plan_id, name="plan_id")
        if plan_dir.exists():
            raise ArtifactError("plan_exists", f"plan directory already exists: {plan_dir}")
        attempts_root = plan_dir / "attempts"
        attempts_root.mkdir(parents=True, exist_ok=False)

        attempt_results: list[SessionRecordResult] = []
        attempt_entries: list[dict[str, Any]] = []
        for attempt in plan.attempts:
            definition = get_scenario_definition(attempt.scenario_id)
            source = SimulatorDataSource(attempt.config)
            directory_name = f"attempt-{attempt.attempt_index:02d}-{source.session_id}"
            result = self.record(
                source,
                output_root=attempts_root,
                definition=definition,
                directory_name=directory_name,
            )
            relative = f"attempts/{result.session_path.name}"
            attempt_results.append(result)
            attempt_entries.append(
                {
                    "attempt_index": attempt.attempt_index,
                    "scenario_id": attempt.scenario_id,
                    "session_id": result.session_id,
                    "relative_path": relative,
                    "configuration_digest": result.configuration_digest,
                    "completed": result.completed,
                    "completion_reason": result.completion_reason,
                    "sample_count": result.sample_count,
                }
            )

        expected_doc = build_plan_expected_artifact(plan)
        validate_expected_artifact(expected_doc)
        write_text_atomic(plan_dir / "expected.json", dumps_compact(expected_doc) + "\n")

        plan_doc = {
            "artifact_version": ARTIFACT_FORMAT_VERSION,
            "artifact_role": "simulator_plan",
            "plan_id": plan_id,
            "plan_version": plan.plan_version,
            "case_id": plan_id,
            "max_attempts": plan.max_attempts,
            "attempt_count": len(attempt_results),
            "attempts": attempt_entries,
            "expected_final_quality": plan.expected_quality_label.value,
            "expected_final_action": plan.expected_int_action.value,
            "analysis_allowed": plan.analysis_allowed,
            "expected_completion": plan.expected_completion,
            "limitations": [
                LimitationCode.SYNTHETIC_INPUT.value,
                LimitationCode.PENDING_H1_CALIBRATION.value,
                LimitationCode.NOT_HARDWARE_VALIDATED.value,
                LimitationCode.NOT_FOR_MEDICAL_USE.value,
            ],
        }
        validate_plan_artifact(plan_doc)
        write_text_atomic(plan_dir / "plan.json", dumps_compact(plan_doc) + "\n")
        return PlanRecordResult(
            plan_id=plan_id,
            plan_path=plan_dir,
            attempt_results=tuple(attempt_results),
            expected_completion=plan.expected_completion,
        )

    def _write_events(self, path: Path, events: tuple[SimulationEvent, ...]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                row = event_to_artifact_row(event)
                validate_event_artifact(row)
                handle.write(dumps_compact(row) + "\n")
