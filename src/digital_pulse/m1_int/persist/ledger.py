"""P4B-B machine decision persistence: append-only, idempotent, crash-safe."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable, Mapping

from digital_pulse.m1_contracts import (
    DecisionAction,
    I1_ACTIONS,
    M1ContractError,
    M1Decision,
    from_dict_decision,
)
from digital_pulse.m1_int.errors import M1IntError
from digital_pulse.m1_int.ledger_models import (
    EMPTY_LEDGER_DIGEST,
    LEDGER_MANIFEST_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    IntLedgerEvent,
    IntLedgerManifest,
    build_int_ledger_event,
    require_machine_decision_record,
    validate_int_ledger_event,
    validate_int_ledger_manifest,
)
from digital_pulse.m1_int.models import (
    DECISION_ID_PATTERN,
    GIT_COMMIT_SHA_PATTERN,
    HEX64_PATTERN,
    DecisionSourceProvenance,
    dumps_canonical,
)

from .locking import int_session_lock

FailureInjector = Callable[[str], None]
PENDING_SCHEMA_VERSION = "i1-ledger-pending-1.0.0-pre"
AWAITING_ACTIONS = frozenset({DecisionAction.MANUAL_REVIEW.value, DecisionAction.REPOSITION.value})
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{value}" for value in range(1, 10)),
    *(f"LPT{value}" for value in range(1, 10)),
}


class AppendStatus(str, Enum):
    COMMITTED = "committed"
    ALREADY_COMMITTED = "already_committed"


COMMITTED = AppendStatus.COMMITTED
ALREADY_COMMITTED = AppendStatus.ALREADY_COMMITTED


@dataclass(frozen=True, slots=True)
class AppendResult:
    status: AppendStatus
    decision_id: str
    event_seq: int
    awaiting_operator_seq: int | None


@dataclass(frozen=True, slots=True)
class _JsonlView:
    records: tuple[dict[str, Any], ...]
    complete_bytes: bytes
    trailing_partial: bytes


class DecisionLedger:
    """Session INT ledger for machine M1Decision append only."""

    def __init__(
        self,
        sessions_root: Path,
        *,
        clock: Callable[[], str],
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self._sessions_root = Path(sessions_root)
        self._clock = clock
        self._failure_injector = failure_injector

    def append_decision(
        self,
        decision: M1Decision,
        source_provenance: DecisionSourceProvenance,
    ) -> AppendResult:
        session_id = _require_session_id(decision.session_id)
        int_dir = self._int_dir(session_id, create=True)
        with int_session_lock(int_dir):
            self._recover_locked(session_id, int_dir)
            return self._append_locked(int_dir, decision, source_provenance)

    def recover_pending_commit(self, session_id: str) -> None:
        session_id = _require_session_id(session_id)
        int_dir = self._int_dir(session_id, create=True)
        with int_session_lock(int_dir):
            self._recover_locked(session_id, int_dir)

    def load_machine_decision(self, session_id: str, decision_id: str) -> M1Decision:
        session_id = _require_session_id(session_id)
        int_dir = self._int_dir(session_id, create=False)
        with int_session_lock(int_dir):
            self._recover_locked(session_id, int_dir)
            decisions = self._read_jsonl(int_dir / "decisions.jsonl")
            for record in decisions.records:
                if record.get("decision_id") == decision_id:
                    return _decision_from_record(record)
        raise M1IntError("invalid_input", "machine decision is not present in the ledger")

    def verify_decision_ledger_minimal(self, session_id: str) -> IntLedgerManifest:
        session_id = _require_session_id(session_id)
        int_dir = self._int_dir(session_id, create=False)
        with int_session_lock(int_dir):
            self._recover_locked(session_id, int_dir)
            return self._verify_locked(session_id, int_dir)

    def _append_locked(
        self,
        int_dir: Path,
        decision: M1Decision,
        source_provenance: DecisionSourceProvenance,
    ) -> AppendResult:
        require_machine_decision_record(decision)
        _validate_append_inputs(decision, source_provenance)
        decisions_path = int_dir / "decisions.jsonl"
        events_path = int_dir / "decision-events.jsonl"
        decisions = self._read_jsonl(decisions_path)
        events = self._read_jsonl(events_path)
        if decisions.trailing_partial or events.trailing_partial:
            raise M1IntError("ledger_untrusted", "ledger has an unrecovered trailing partial record")
        existing = _index_decisions(decisions.records)
        decision_line = _decision_line_bytes(decision)
        prior = existing.get(decision.decision_id)
        if prior is not None:
            if prior == decision_line:
                _assert_retry_provenance_matches(events.records, decision.decision_id, source_provenance)
                recorded_seq = _recorded_seq_for(events.records, decision.decision_id)
                awaiting_seq = _awaiting_seq_for(events.records, decision.decision_id)
                return AppendResult(
                    status=AppendStatus.ALREADY_COMMITTED,
                    decision_id=decision.decision_id,
                    event_seq=recorded_seq,
                    awaiting_operator_seq=awaiting_seq,
                )
            raise M1IntError("duplicate_conflict", "decision_id already exists with different payload")

        self._assert_minimal_integrity(decision.session_id, decisions, events)
        next_seq = (events.records[-1]["event_seq"] + 1) if events.records else 1
        occurred_at = self._clock()
        recorded = _build_recorded_event(decision, source_provenance, next_seq, occurred_at)
        event_lines = [_event_line_bytes(recorded)]
        awaiting_seq: int | None = None
        action = _action_value(decision.action)
        if action in AWAITING_ACTIONS:
            awaiting_seq = next_seq + 1
            awaiting = _build_awaiting_event(decision, source_provenance, awaiting_seq, occurred_at)
            event_lines.append(_event_line_bytes(awaiting))

        pending = _pending_descriptor(
            session_id=decision.session_id,
            decision_id=decision.decision_id,
            pre_decisions=decisions.complete_bytes,
            pre_events=events.complete_bytes,
            decision_line=decision_line,
            event_lines=event_lines,
            rule_version=decision.rule_version,
            configuration_digest=decision.input_versions.configuration_digest,
            software_commit_sha=source_provenance.software_commit_sha,
        )
        self._write_pending(int_dir, pending)
        self._commit_pending(int_dir, pending)
        return AppendResult(
            status=AppendStatus.COMMITTED,
            decision_id=decision.decision_id,
            event_seq=next_seq,
            awaiting_operator_seq=awaiting_seq,
        )

    def _recover_locked(self, session_id: str, int_dir: Path) -> None:
        decisions_path = int_dir / "decisions.jsonl"
        events_path = int_dir / "decision-events.jsonl"
        pending_path = int_dir / ".pending-commit.json"
        pending = self._load_pending(pending_path)
        decisions = self._read_jsonl(decisions_path)
        events = self._read_jsonl(events_path)
        if pending is None:
            if decisions.trailing_partial or events.trailing_partial:
                raise M1IntError(
                    "ledger_untrusted",
                    "trailing partial record exists without a bound pending commit",
                )
            if (int_dir / "manifest.json").exists() or decisions.records or events.records:
                self._reconcile_manifest(session_id, int_dir)
            return

        if pending.get("session_id") != session_id:
            raise M1IntError("ledger_untrusted", "pending commit session_id does not match ledger")
        if pending.get("schema_version") != PENDING_SCHEMA_VERSION:
            raise M1IntError("version_mismatch", "pending commit schema is not supported")
        decision_line = _b64_or_text(pending["decision_record"])
        event_lines = [_b64_or_text(item) for item in pending["event_records"]]
        _require_pending_intended_records(decision_line, event_lines)
        pre_decision_count = int(pending["pre_decision_count"])
        pre_event_count = int(pending["pre_event_count"])
        decisions = self._recover_decision_trailing(
            decisions_path, decisions, decision_line, pre_decision_count
        )
        events = self._recover_event_trailing(events_path, events, event_lines, pre_event_count)
        self._apply_pending(int_dir, pending, decisions, events, event_lines)

    def _recover_decision_trailing(
        self,
        path: Path,
        view: _JsonlView,
        intended: bytes,
        pre_decision_count: int,
    ) -> _JsonlView:
        if not view.trailing_partial:
            return view
        if len(view.records) == pre_decision_count and intended.startswith(view.trailing_partial):
            self._truncate_to(path, view.complete_bytes)
            return self._read_jsonl(path)
        raise M1IntError("ledger_untrusted", "trailing partial decision record is not a recoverable prefix")

    def _recover_event_trailing(
        self,
        path: Path,
        view: _JsonlView,
        event_lines: list[bytes],
        pre_event_count: int,
    ) -> _JsonlView:
        if not view.trailing_partial:
            return view
        written_new = len(view.records) - pre_event_count
        if 0 <= written_new < len(event_lines) and event_lines[written_new].startswith(view.trailing_partial):
            self._truncate_to(path, view.complete_bytes)
            return self._read_jsonl(path)
        raise M1IntError("ledger_untrusted", "trailing partial event record is not a recoverable prefix")

    def _apply_pending(
        self,
        int_dir: Path,
        pending: dict[str, Any],
        decisions: _JsonlView,
        events: _JsonlView,
        event_lines: list[bytes],
    ) -> None:
        pre_d = _hex_digest(pending["pre_decisions_sha256"])
        pre_e = _hex_digest(pending["pre_events_sha256"])
        post_d = _hex_digest(pending["post_decisions_sha256"])
        post_e = _hex_digest(pending["post_events_sha256"])
        actual_d = _sha256_bytes(decisions.complete_bytes)
        actual_e = _sha256_bytes(events.complete_bytes)
        pre_event_count = int(pending["pre_event_count"])
        intended_events = b"".join(event_lines)
        if actual_d == pre_d and actual_e == pre_e:
            self._commit_pending(int_dir, pending)
            return
        if actual_d == post_d and actual_e == pre_e:
            self._append_exact(int_dir / "decision-events.jsonl", intended_events, "events")
            self._write_manifest_from_pending(int_dir, pending)
            self._clear_pending(int_dir)
            return
        if actual_d == post_d and actual_e == post_e:
            self._write_manifest_from_pending(int_dir, pending)
            self._clear_pending(int_dir)
            return
        written_new = len(events.records) - pre_event_count
        if actual_d == post_d and 0 < written_new < len(event_lines):
            written_tail = b"".join(
                (dumps_canonical(record) + "\n").encode("utf-8")
                for record in events.records[pre_event_count:]
            )
            expected_tail = b"".join(event_lines[:written_new])
            if written_tail != expected_tail:
                raise M1IntError(
                    "ledger_untrusted",
                    "partial event prefix does not match pending intended bytes",
                )
            remainder = b"".join(event_lines[written_new:])
            if remainder:
                self._append_exact(int_dir / "decision-events.jsonl", remainder, "events")
            self._write_manifest_from_pending(int_dir, pending)
            self._clear_pending(int_dir)
            return
        raise M1IntError("ledger_untrusted", "pending commit does not match ledger pre/post state")

    def _commit_pending(self, int_dir: Path, pending: dict[str, Any]) -> None:
        decision_line = _b64_or_text(pending["decision_record"])
        event_lines = [_b64_or_text(item) for item in pending["event_records"]]
        self._append_exact(int_dir / "decisions.jsonl", decision_line, "decisions")
        self._append_exact(int_dir / "decision-events.jsonl", b"".join(event_lines), "events")
        self._write_manifest_from_pending(int_dir, pending)
        self._clear_pending(int_dir)

    def _write_pending(self, int_dir: Path, pending: dict[str, Any]) -> None:
        path = int_dir / ".pending-commit.json"
        payload = (dumps_canonical(pending) + "\n").encode("utf-8")
        self._atomic_write(path, payload, write_point="pending_write", fsync_point="pending_fsync")

    def _load_pending(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        if path.is_symlink():
            raise M1IntError("ledger_untrusted", "pending commit path is a symbolic link")
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise M1IntError("ledger_untrusted", "pending commit cannot be read") from exc
        if not isinstance(payload, dict):
            raise M1IntError("ledger_untrusted", "pending commit is not an object")
        return payload

    def _clear_pending(self, int_dir: Path) -> None:
        path = int_dir / ".pending-commit.json"
        self._inject("pending_delete")
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            raise M1IntError("persistence_failure", "pending commit could not be cleared") from exc
        self._fsync_directory(int_dir)

    def _write_manifest_from_pending(self, int_dir: Path, pending: dict[str, Any]) -> None:
        decisions = self._read_jsonl(int_dir / "decisions.jsonl")
        events = self._read_jsonl(int_dir / "decision-events.jsonl")
        if decisions.trailing_partial or events.trailing_partial:
            raise M1IntError("ledger_untrusted", "cannot write manifest over a partial ledger")
        manifest = _manifest_from_ledger(
            session_id=pending["session_id"],
            decisions=decisions,
            events=events,
            rule_version=pending["decision_rule_version"],
            configuration_digest=pending["configuration_digest"],
            software_commit_sha=pending["software_commit_sha"],
        )
        if _sha256_bytes(decisions.complete_bytes) != pending["post_decisions_sha256"]:
            raise M1IntError("ledger_untrusted", "committed decisions digest does not match pending target")
        if _sha256_bytes(events.complete_bytes) != pending["post_events_sha256"]:
            raise M1IntError("ledger_untrusted", "committed events digest does not match pending target")
        self._write_manifest(int_dir, manifest)

    def _reconcile_manifest(self, session_id: str, int_dir: Path) -> None:
        decisions = self._read_jsonl(int_dir / "decisions.jsonl")
        events = self._read_jsonl(int_dir / "decision-events.jsonl")
        if decisions.trailing_partial or events.trailing_partial:
            raise M1IntError("ledger_untrusted", "cannot reconcile manifest over a partial ledger")
        if not decisions.records and not events.records:
            return
        # Crash-consistency only: rebuild a stale/missing manifest from JSONL
        # that already passed minimal integrity. This is not tamper evidence.
        self._assert_minimal_integrity(session_id, decisions, events)
        rule_version, config, software = _provenance_from_events(events.records)
        manifest = _manifest_from_ledger(
            session_id=session_id,
            decisions=decisions,
            events=events,
            rule_version=rule_version,
            configuration_digest=config,
            software_commit_sha=software,
        )
        existing = int_dir / "manifest.json"
        if existing.exists():
            try:
                current = _load_manifest(existing)
            except M1IntError:
                current = None
            if current == manifest:
                return
        self._write_manifest(int_dir, manifest)

    def _write_manifest(self, int_dir: Path, manifest: IntLedgerManifest) -> None:
        validate_int_ledger_manifest(manifest)
        payload = (dumps_canonical(_manifest_payload(manifest)) + "\n").encode("utf-8")
        self._atomic_write(
            int_dir / "manifest.json",
            payload,
            write_point="manifest_write",
            fsync_point="manifest_fsync",
        )
        self._fsync_directory(int_dir)

    def _verify_locked(self, session_id: str, int_dir: Path) -> IntLedgerManifest:
        decisions = self._read_jsonl(int_dir / "decisions.jsonl")
        events = self._read_jsonl(int_dir / "decision-events.jsonl")
        if decisions.trailing_partial or events.trailing_partial:
            raise M1IntError("ledger_untrusted", "ledger still has a trailing partial record")
        self._assert_minimal_integrity(session_id, decisions, events)
        manifest = _load_manifest(int_dir / "manifest.json")
        expected = _manifest_from_ledger(
            session_id=session_id,
            decisions=decisions,
            events=events,
            rule_version=manifest.decision_rule_version,
            configuration_digest=manifest.configuration_digest,
            software_commit_sha=manifest.software_commit_sha,
        )
        if _manifest_payload(manifest) != _manifest_payload(expected):
            raise M1IntError("ledger_untrusted", "manifest does not match ledger source of truth")
        return manifest

    def _assert_minimal_integrity(self, session_id: str, decisions: _JsonlView, events: _JsonlView) -> None:
        seen: set[str] = set()
        for index, record in enumerate(decisions.records):
            if record.get("session_id") != session_id:
                raise M1IntError("ledger_untrusted", "decision session_id does not match ledger")
            decision = _decision_from_record(record)
            if decision.decision_id in seen:
                raise M1IntError("ledger_untrusted", "decision_id is duplicated in decisions.jsonl")
            seen.add(decision.decision_id)
            expected = _decision_line_bytes(decision)
            actual_line = dumps_canonical(record).encode("utf-8") + b"\n"
            if actual_line != expected:
                raise M1IntError("ledger_untrusted", "decision record is not canonical")
            del index
        expected_seq = 1
        recorded_seq_by_id: dict[str, int] = {}
        awaiting_seq_by_id: dict[str, int] = {}
        for record in events.records:
            event = _event_from_record(record)
            if event.session_id != session_id:
                raise M1IntError("ledger_untrusted", "event session_id does not match ledger")
            if event.event_seq != expected_seq:
                raise M1IntError("ledger_untrusted", "event_seq is not contiguous")
            if event.event_type not in {"decision_recorded", "awaiting_operator"}:
                raise M1IntError("ledger_untrusted", "P4B-B ledger contains a non-decision-append event")
            if event.event_type == "decision_recorded":
                if event.decision_id not in seen:
                    raise M1IntError("ledger_untrusted", "decision_recorded references an unknown decision")
                if event.decision_id in recorded_seq_by_id:
                    raise M1IntError("ledger_untrusted", "decision_recorded is duplicated for one decision")
                recorded_seq_by_id[event.decision_id or ""] = event.event_seq
            elif event.event_type == "awaiting_operator":
                if event.decision_id not in recorded_seq_by_id:
                    raise M1IntError("ledger_untrusted", "awaiting_operator precedes decision_recorded")
                if event.decision_id in awaiting_seq_by_id:
                    raise M1IntError("ledger_untrusted", "awaiting_operator is duplicated for one decision")
                recorded_seq = recorded_seq_by_id[event.decision_id or ""]
                if event.event_seq != recorded_seq + 1:
                    raise M1IntError("ledger_untrusted", "awaiting_operator is not the next event after decision_recorded")
                awaiting_seq_by_id[event.decision_id or ""] = event.event_seq
            expected_seq += 1
        if seen - set(recorded_seq_by_id):
            raise M1IntError("ledger_untrusted", "decision exists without decision_recorded")
        for record in decisions.records:
            decision = _decision_from_record(record)
            action = _action_value(decision.action)
            has_awaiting = decision.decision_id in awaiting_seq_by_id
            if action in AWAITING_ACTIONS and not has_awaiting:
                raise M1IntError("ledger_untrusted", "manual_review/reposition is missing awaiting_operator")
            if action not in AWAITING_ACTIONS and has_awaiting:
                raise M1IntError("ledger_untrusted", "awaiting_operator is not allowed for this action")

    def _read_jsonl(self, path: Path) -> _JsonlView:
        if not path.exists():
            return _JsonlView((), b"", b"")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise M1IntError("persistence_failure", "ledger file cannot be read") from exc
        if not data:
            return _JsonlView((), b"", b"")
        if data.endswith(b"\n"):
            complete, trailing = data, b""
        else:
            last_nl = data.rfind(b"\n")
            if last_nl == -1:
                complete, trailing = b"", data
            else:
                complete, trailing = data[: last_nl + 1], data[last_nl + 1 :]
        records: list[dict[str, Any]] = []
        for raw_line in complete.split(b"\n"):
            if raw_line == b"":
                continue
            try:
                text = raw_line.decode("utf-8")
                payload = json.loads(text)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise M1IntError("ledger_untrusted", "complete ledger line is corrupt") from exc
            if not isinstance(payload, dict):
                raise M1IntError("ledger_untrusted", "ledger record must be a JSON object")
            records.append(payload)
        return _JsonlView(tuple(records), complete, trailing)

    def _append_exact(self, path: Path, data: bytes, kind: str) -> None:
        if not data:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(data)
                handle.flush()
                self._inject(f"{kind}_append")
                os.fsync(handle.fileno())
                self._inject(f"{kind}_fsync")
        except M1IntError:
            raise
        except OSError as exc:
            raise M1IntError("persistence_failure", f"{kind} append failed") from exc

    def _truncate_to(self, path: Path, data: bytes) -> None:
        try:
            with path.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise M1IntError("persistence_failure", "partial ledger tail could not be truncated") from exc

    def _atomic_write(self, path: Path, data: bytes, *, write_point: str, fsync_point: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            self._inject(write_point)
            with tmp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                self._inject(fsync_point)
            os.replace(tmp, path)
        except M1IntError:
            _unlink_quiet(tmp)
            raise
        except OSError as exc:
            _unlink_quiet(tmp)
            raise M1IntError("persistence_failure", "atomic ledger write failed") from exc

    def _int_dir(self, session_id: str, *, create: bool) -> Path:
        root = self._sessions_root.resolve()
        if _is_link_or_junction(root) if root.exists() else False:
            raise M1IntError("invalid_input", "sessions root is a symbolic link or junction")
        session_root = root / session_id
        if session_root.exists() and _is_link_or_junction(session_root):
            raise M1IntError("invalid_input", "session path is a symbolic link or junction")
        int_dir = session_root / "int"
        if create:
            try:
                int_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise M1IntError("persistence_failure", "INT directory could not be created") from exc
        elif not int_dir.is_dir():
            raise M1IntError("invalid_input", "INT ledger directory does not exist")
        if _is_link_or_junction(int_dir):
            raise M1IntError("invalid_input", "INT directory is a symbolic link or junction")
        resolved = int_dir.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise M1IntError("invalid_input", "INT path escapes the sessions root") from exc
        return resolved

    def _inject(self, point: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(point)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _validate_append_inputs(decision: M1Decision, provenance: DecisionSourceProvenance) -> None:
    if not DECISION_ID_PATTERN.fullmatch(decision.decision_id):
        raise M1IntError("invalid_input", "decision_id is not a frozen machine decision identity")
    action = _action_value(decision.action)
    if action not in I1_ACTIONS:
        raise M1IntError("invalid_input", "action is not a frozen I1 action")
    if decision.int_level != "I1":
        raise M1IntError("invalid_input", "P4B-B only persists I1 machine decisions")
    if not GIT_COMMIT_SHA_PATTERN.fullmatch(provenance.software_commit_sha):
        raise M1IntError("invalid_input", "software_commit_sha must be a 40-hex commit SHA")
    for field_name in ("app_analysis_fingerprint", "sp_result_fingerprint"):
        value = getattr(provenance, field_name)
        if value is not None and (not HEX64_PATTERN.fullmatch(value) or value == "0" * 64):
            raise M1IntError("invalid_input", f"{field_name} must be a non-zero 64-hex digest")
    if provenance.app_run_id is not None and not str(provenance.app_run_id).strip():
        raise M1IntError("invalid_input", "app_run_id must be a non-empty string when present")


def _build_recorded_event(
    decision: M1Decision,
    provenance: DecisionSourceProvenance,
    event_seq: int,
    occurred_at: str,
) -> IntLedgerEvent:
    return build_int_ledger_event(
        event_seq=event_seq,
        event_type="decision_recorded",
        session_id=decision.session_id,
        occurred_at_utc=occurred_at,
        decision_id=decision.decision_id,
        software_commit_sha=provenance.software_commit_sha,
        rule_version=decision.rule_version,
        configuration_digest=decision.input_versions.configuration_digest,
        app_run_id=provenance.app_run_id,
        app_analysis_fingerprint=provenance.app_analysis_fingerprint,
        sp_result_fingerprint=provenance.sp_result_fingerprint,
    )


def _build_awaiting_event(
    decision: M1Decision,
    provenance: DecisionSourceProvenance,
    event_seq: int,
    occurred_at: str,
) -> IntLedgerEvent:
    return build_int_ledger_event(
        event_seq=event_seq,
        event_type="awaiting_operator",
        session_id=decision.session_id,
        occurred_at_utc=occurred_at,
        decision_id=decision.decision_id,
        outcome="awaiting_operator",
        software_commit_sha=provenance.software_commit_sha,
        rule_version=decision.rule_version,
        configuration_digest=decision.input_versions.configuration_digest,
        app_run_id=provenance.app_run_id,
        app_analysis_fingerprint=provenance.app_analysis_fingerprint,
        sp_result_fingerprint=provenance.sp_result_fingerprint,
    )


def _pending_descriptor(
    *,
    session_id: str,
    decision_id: str,
    pre_decisions: bytes,
    pre_events: bytes,
    decision_line: bytes,
    event_lines: list[bytes],
    rule_version: str,
    configuration_digest: str,
    software_commit_sha: str,
) -> dict[str, Any]:
    post_decisions = pre_decisions + decision_line
    post_events = pre_events + b"".join(event_lines)
    payload = {
        "schema_version": PENDING_SCHEMA_VERSION,
        "session_id": session_id,
        "decision_id": decision_id,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "pre_decision_count": pre_decisions.count(b"\n"),
        "pre_event_count": pre_events.count(b"\n"),
        "pre_last_event_seq": pre_events.count(b"\n"),
        "pre_decisions_sha256": _sha256_bytes(pre_decisions),
        "pre_events_sha256": _sha256_bytes(pre_events),
        "decision_record": decision_line.decode("utf-8"),
        "event_records": [line.decode("utf-8") for line in event_lines],
        "post_decision_count": post_decisions.count(b"\n"),
        "post_event_count": post_events.count(b"\n"),
        "post_last_event_seq": post_events.count(b"\n"),
        "post_decisions_sha256": _sha256_bytes(post_decisions),
        "post_events_sha256": _sha256_bytes(post_events),
        "decision_rule_version": rule_version,
        "configuration_digest": configuration_digest,
        "software_commit_sha": software_commit_sha,
    }
    digest = hashlib.sha256(dumps_canonical(payload).encode("utf-8")).hexdigest()
    payload["commit_id"] = "m1-int-commit-" + digest
    return payload


def _manifest_from_ledger(
    *,
    session_id: str,
    decisions: _JsonlView,
    events: _JsonlView,
    rule_version: str,
    configuration_digest: str,
    software_commit_sha: str,
) -> IntLedgerManifest:
    current = decisions.records[-1]["decision_id"] if decisions.records else None
    last_seq = events.records[-1]["event_seq"] if events.records else 0
    return validate_int_ledger_manifest(
        IntLedgerManifest(
            schema_version=LEDGER_MANIFEST_SCHEMA_VERSION,
            session_id=session_id,
            decision_rule_version=rule_version,
            configuration_digest=configuration_digest,
            software_commit_sha=software_commit_sha,
            decisions_sha256=_sha256_bytes(decisions.complete_bytes),
            events_sha256=_sha256_bytes(events.complete_bytes),
            decision_count=len(decisions.records),
            event_count=len(events.records),
            last_event_seq=last_seq,
            current_decision_id=current,
        )
    )


def _manifest_payload(manifest: IntLedgerManifest) -> dict[str, Any]:
    return {
        "configuration_digest": manifest.configuration_digest,
        "current_decision_id": manifest.current_decision_id,
        "decision_count": manifest.decision_count,
        "decision_rule_version": manifest.decision_rule_version,
        "decisions_sha256": manifest.decisions_sha256,
        "event_count": manifest.event_count,
        "events_sha256": manifest.events_sha256,
        "last_event_seq": manifest.last_event_seq,
        "schema_version": manifest.schema_version,
        "session_id": manifest.session_id,
        "software_commit_sha": manifest.software_commit_sha,
    }


def _load_manifest(path: Path) -> IntLedgerManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M1IntError("ledger_untrusted", "manifest cannot be read") from exc
    if not isinstance(payload, dict):
        raise M1IntError("ledger_untrusted", "manifest must be a JSON object")
    try:
        manifest = IntLedgerManifest(
            schema_version=payload["schema_version"],
            session_id=payload["session_id"],
            decision_rule_version=payload["decision_rule_version"],
            configuration_digest=payload["configuration_digest"],
            software_commit_sha=payload["software_commit_sha"],
            decisions_sha256=payload["decisions_sha256"],
            events_sha256=payload["events_sha256"],
            decision_count=payload["decision_count"],
            event_count=payload["event_count"],
            last_event_seq=payload["last_event_seq"],
            current_decision_id=payload.get("current_decision_id"),
        )
    except (KeyError, TypeError) as exc:
        raise M1IntError("ledger_untrusted", "manifest fields are incomplete") from exc
    return validate_int_ledger_manifest(manifest)


def _decision_line_bytes(decision: M1Decision) -> bytes:
    return (dumps_canonical(decision.to_dict()) + "\n").encode("utf-8")


def _event_line_bytes(event: IntLedgerEvent) -> bytes:
    payload: dict[str, Any] = {}
    for field in fields(event):
        value = getattr(event, field.name)
        if value is None:
            continue
        payload[field.name] = value
    return (dumps_canonical(payload) + "\n").encode("utf-8")


def _decision_from_record(record: Mapping[str, Any]) -> M1Decision:
    try:
        return from_dict_decision(record)
    except (M1ContractError, KeyError, TypeError, ValueError) as exc:
        raise M1IntError("ledger_untrusted", "decision record failed P0 validation") from exc


def _event_from_record(record: Mapping[str, Any]) -> IntLedgerEvent:
    known = {item.name for item in fields(IntLedgerEvent)}
    extra = set(record) - known
    if extra:
        raise M1IntError("ledger_untrusted", "event record has unknown fields")
    try:
        event = IntLedgerEvent(**{name: record.get(name) for name in known if name in record or name in {
            "event_id",
            "event_seq",
            "event_type",
            "session_id",
            "ledger_schema_version",
            "occurred_at_utc",
        }})
        return validate_int_ledger_event(event)
    except (TypeError, M1IntError) as exc:
        raise M1IntError("ledger_untrusted", "event record failed ledger validation") from exc


def _index_decisions(records: tuple[dict[str, Any], ...]) -> dict[str, bytes]:
    indexed: dict[str, bytes] = {}
    for record in records:
        decision = _decision_from_record(record)
        indexed[decision.decision_id] = _decision_line_bytes(decision)
    return indexed


def _assert_retry_provenance_matches(
    records: tuple[dict[str, Any], ...],
    decision_id: str,
    provenance: DecisionSourceProvenance,
) -> None:
    recorded = None
    for record in records:
        if record.get("event_type") == "decision_recorded" and record.get("decision_id") == decision_id:
            recorded = record
            break
    if recorded is None:
        raise M1IntError("ledger_untrusted", "committed decision is missing decision_recorded")
    expected = {
        "app_analysis_fingerprint": provenance.app_analysis_fingerprint,
        "app_run_id": provenance.app_run_id,
        "software_commit_sha": provenance.software_commit_sha,
        "sp_result_fingerprint": provenance.sp_result_fingerprint,
    }
    actual = {key: recorded.get(key) for key in expected}
    if actual != expected:
        raise M1IntError(
            "provenance_mismatch",
            "retry provenance does not match the committed decision_recorded audit fact",
        )


def _require_pending_intended_records(decision_line: bytes, event_lines: list[bytes]) -> None:
    try:
        decision_payload = json.loads(decision_line.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise M1IntError("ledger_untrusted", "pending decision record is not JSON") from exc
    if not isinstance(decision_payload, dict):
        raise M1IntError("ledger_untrusted", "pending decision record is not an object")
    decision = _decision_from_record(decision_payload)
    try:
        require_machine_decision_record(decision)
    except M1IntError as exc:
        raise M1IntError("ledger_untrusted", "pending decision is not a machine decision record") from exc
    if _decision_line_bytes(decision) != decision_line:
        raise M1IntError("ledger_untrusted", "pending decision record is not canonical")
    if not event_lines:
        raise M1IntError("ledger_untrusted", "pending commit has no intended events")
    parsed_events = []
    for raw in event_lines:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise M1IntError("ledger_untrusted", "pending event record is not JSON") from exc
        if not isinstance(payload, dict):
            raise M1IntError("ledger_untrusted", "pending event record is not an object")
        event = _event_from_record(payload)
        if _event_line_bytes(event) != raw:
            raise M1IntError("ledger_untrusted", "pending event record is not canonical")
        parsed_events.append(event)
    recorded = parsed_events[0]
    if recorded.event_type != "decision_recorded" or recorded.decision_id != decision.decision_id:
        raise M1IntError("ledger_untrusted", "pending first event is not decision_recorded for the decision")
    action = _action_value(decision.action)
    if action in AWAITING_ACTIONS:
        if len(parsed_events) != 2 or parsed_events[1].event_type != "awaiting_operator":
            raise M1IntError("ledger_untrusted", "pending companion awaiting_operator is missing")
        if parsed_events[1].decision_id != decision.decision_id:
            raise M1IntError("ledger_untrusted", "pending awaiting_operator points at the wrong decision")
        if parsed_events[1].event_seq != recorded.event_seq + 1:
            raise M1IntError("ledger_untrusted", "pending awaiting_operator sequence is not contiguous")
    elif len(parsed_events) != 1:
        raise M1IntError("ledger_untrusted", "pending contains an unexpected companion event")


def _recorded_seq_for(records: tuple[dict[str, Any], ...], decision_id: str) -> int:
    for record in records:
        if record.get("event_type") == "decision_recorded" and record.get("decision_id") == decision_id:
            return int(record["event_seq"])
    raise M1IntError("ledger_untrusted", "committed decision is missing decision_recorded")


def _awaiting_seq_for(records: tuple[dict[str, Any], ...], decision_id: str) -> int | None:
    for record in records:
        if record.get("event_type") == "awaiting_operator" and record.get("decision_id") == decision_id:
            return int(record["event_seq"])
    return None


def _provenance_from_events(records: tuple[dict[str, Any], ...]) -> tuple[str, str, str]:
    for record in reversed(records):
        if record.get("event_type") == "decision_recorded":
            rule = record.get("rule_version")
            digest = record.get("configuration_digest")
            software = record.get("software_commit_sha")
            if isinstance(rule, str) and isinstance(digest, str) and isinstance(software, str):
                return rule, digest, software
    raise M1IntError("ledger_untrusted", "ledger events lack decision_recorded provenance")


def _require_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise M1IntError("invalid_input", "session_id is not a portable INT path segment")
    if "/" in session_id or "\\" in session_id or ":" in session_id:
        raise M1IntError("invalid_input", "session_id is not a portable INT path segment")
    if session_id in {".", ".."} or any(ord(char) < 32 or char in '<>"|?*' for char in session_id):
        raise M1IntError("invalid_input", "session_id contains an unsafe character")
    if session_id.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise M1IntError("invalid_input", "session_id is a reserved Windows name")
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise M1IntError("invalid_input", "session_id is not a portable INT path segment")
    if PurePosixPath(session_id).as_posix() != session_id:
        raise M1IntError("invalid_input", "session_id is not a canonical path segment")
    return session_id


def _action_value(action: DecisionAction | str) -> str:
    return action.value if isinstance(action, DecisionAction) else str(action)


def _sha256_bytes(data: bytes) -> str:
    if not data:
        return EMPTY_LEDGER_DIGEST
    return hashlib.sha256(data).hexdigest()


def _hex_digest(value: Any) -> str:
    if not isinstance(value, str) or not HEX64_PATTERN.fullmatch(value):
        raise M1IntError("ledger_untrusted", "pending digest is not a 64-hex SHA256")
    return value


def _b64_or_text(value: Any) -> bytes:
    if not isinstance(value, str):
        raise M1IntError("ledger_untrusted", "pending record is not text")
    encoded = value.encode("utf-8")
    if not encoded.endswith(b"\n"):
        encoded += b"\n"
    return encoded


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _unlink_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
