"""P4B-A INT ledger 事件与 manifest 纯合同。不写盘、不重放、不编排。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import re
from typing import Any

from digital_pulse.m1_contracts import I1_ACTIONS, M1ContractError, M1Decision

from .errors import M1IntError
from .models import (
    DECISION_ID_PATTERN,
    GIT_COMMIT_SHA_PATTERN,
    HEX64_PATTERN,
    dumps_canonical,
    sha256_canonical,
)

# Ledger 层 schema，不得复用 M1Decision.schema_version。
LEDGER_SCHEMA_VERSION = "i1-ledger-1.0.0-pre"
LEDGER_MANIFEST_SCHEMA_VERSION = "i1-ledger-manifest-1.0.0-pre"
EMPTY_LEDGER_DIGEST = hashlib.sha256(b"").hexdigest()

FROZEN_EVENT_TYPES = frozenset(
    {
        "decision_recorded",
        "operator_override",
        "action_applied",
        "action_rejected_by_safety",
        "decision_completed",
        "awaiting_operator",
        "reposition_acknowledged",
        "manual_review_resolved",
        "retry_scope_started",
        "retry_scope_closed",
        "retry_attempt_linked",
    }
)
FROZEN_OUTCOMES = frozenset(
    {
        None,
        "awaiting_operator",
        "applied",
        "superseded",
        "rejected_by_safety",
        "completed",
    }
)
FROZEN_RESOLUTIONS = frozenset(
    {
        "remain_awaiting",
        "terminate_stop",
        "continue_new_acquisition",
    }
)
RETRY_SCOPE_ID_PATTERN = re.compile(r"^m1-retry-scope-[0-9a-f]{64}$")
EVENT_ID_PREFIX = "m1-int-event-"

# 身份字段：语义事实。不含 event_id、墙钟、软件 SHA、路径。
_IDENTITY_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "decision_recorded": ("decision_id",),
    "operator_override": ("decision_id", "note", "operator_id", "requested_action"),
    "action_rejected_by_safety": ("decision_id", "note", "operator_id", "requested_action"),
    "action_applied": ("decision_id", "outcome"),
    "decision_completed": ("decision_id", "outcome"),
    "awaiting_operator": ("decision_id", "outcome"),
    "reposition_acknowledged": ("decision_id", "new_session_id", "operator_id", "prior_scope_id"),
    "manual_review_resolved": ("decision_id", "operator_id", "resolution"),
    "retry_scope_started": ("prior_scope_id", "retry_scope_id"),
    "retry_scope_closed": ("retry_scope_id",),
    "retry_attempt_linked": ("decision_id", "linked_session_id", "retry_scope_id"),
}
_DEFAULT_OUTCOME_BY_TYPE = {
    "action_applied": "applied",
    "decision_completed": "completed",
    "awaiting_operator": "awaiting_operator",
    "action_rejected_by_safety": "rejected_by_safety",
}
_REQUIRED_DECISION_ID_TYPES = FROZEN_EVENT_TYPES - {"retry_scope_started", "retry_scope_closed"}


@dataclass(frozen=True, slots=True)
class IntLedgerEvent:
    """不可变 INT 决策事件信封。身份与运行时 provenance 分离。"""

    event_id: str
    event_seq: int
    event_type: str
    session_id: str
    ledger_schema_version: str
    occurred_at_utc: str
    decision_id: str | None = None
    requested_action: str | None = None
    operator_id: str | None = None
    note: str | None = None
    resolution: str | None = None
    outcome: str | None = None
    software_commit_sha: str | None = None
    rule_version: str | None = None
    configuration_digest: str | None = None
    app_run_id: str | None = None
    app_analysis_fingerprint: str | None = None
    sp_result_fingerprint: str | None = None
    prior_scope_id: str | None = None
    new_session_id: str | None = None
    retry_scope_id: str | None = None
    linked_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntLedgerManifest:
    """派生索引合同。不是 ledger 真相源；本 slice 不重建、不落盘。"""

    schema_version: str
    session_id: str
    decision_rule_version: str
    configuration_digest: str
    software_commit_sha: str
    decisions_sha256: str
    events_sha256: str
    decision_count: int
    event_count: int
    last_event_seq: int
    current_decision_id: str | None


def require_frozen_outcome(outcome: str | None) -> str | None:
    if outcome not in FROZEN_OUTCOMES:
        raise M1IntError("invalid_input", "outcome is not a frozen engineering workflow value")
    return outcome


def require_frozen_resolution(resolution: str) -> str:
    if resolution not in FROZEN_RESOLUTIONS:
        raise M1IntError("invalid_input", "resolution is not a frozen manual_review value")
    return resolution


def require_machine_decision_record(decision: M1Decision) -> M1Decision:
    """只接受尚未被覆盖/写 outcome 的原始机器决策。"""

    if decision.operator_override is not None:
        raise M1IntError("invalid_input", "machine decision must keep operator_override null")
    if decision.outcome is not None:
        raise M1IntError("invalid_input", "machine decision must keep outcome null")
    try:
        decision.validate()
    except M1ContractError as exc:
        raise M1IntError("invalid_input", "machine decision failed P0 validation") from exc
    return decision


def canonical_event_payload(event: IntLedgerEvent) -> dict[str, Any]:
    """确定性身份载荷：含 event_seq 与语义字段，不含 event_id / 墙钟 / 软件 SHA。"""

    payload: dict[str, Any] = {
        "event_seq": event.event_seq,
        "event_type": event.event_type,
        "ledger_schema_version": event.ledger_schema_version,
        "session_id": event.session_id,
    }
    for field_name in _IDENTITY_FIELDS_BY_TYPE[event.event_type]:
        payload[field_name] = getattr(event, field_name)
    return payload


def canonical_event_bytes(event: IntLedgerEvent) -> bytes:
    return dumps_canonical(canonical_event_payload(event)).encode("utf-8")


def event_fingerprint(event: IntLedgerEvent) -> str:
    return sha256_canonical(canonical_event_payload(event))


def build_int_ledger_event(
    *,
    event_seq: int,
    event_type: str,
    session_id: str,
    occurred_at_utc: str,
    ledger_schema_version: str = LEDGER_SCHEMA_VERSION,
    decision_id: str | None = None,
    requested_action: str | None = None,
    operator_id: str | None = None,
    note: str | None = None,
    resolution: str | None = None,
    outcome: str | None = None,
    software_commit_sha: str | None = None,
    rule_version: str | None = None,
    configuration_digest: str | None = None,
    app_run_id: str | None = None,
    app_analysis_fingerprint: str | None = None,
    sp_result_fingerprint: str | None = None,
    prior_scope_id: str | None = None,
    new_session_id: str | None = None,
    retry_scope_id: str | None = None,
    linked_session_id: str | None = None,
) -> IntLedgerEvent:
    """校验并构造带确定性 event_id 的不可变事件。"""

    _require_positive_seq(event_seq)
    if event_type not in FROZEN_EVENT_TYPES:
        raise M1IntError("invalid_input", "event_type is not in the frozen INT event set")
    if ledger_schema_version != LEDGER_SCHEMA_VERSION:
        raise M1IntError("version_mismatch", "ledger_schema_version does not match P4B ledger schema")
    _require_non_empty("session_id", session_id)
    _require_iso8601_utc(occurred_at_utc)
    resolved_outcome = _resolve_outcome(event_type, outcome)
    draft = IntLedgerEvent(
        event_id="",
        event_seq=event_seq,
        event_type=event_type,
        session_id=session_id,
        ledger_schema_version=ledger_schema_version,
        occurred_at_utc=occurred_at_utc,
        decision_id=decision_id,
        requested_action=requested_action,
        operator_id=operator_id,
        note=note,
        resolution=resolution,
        outcome=resolved_outcome,
        software_commit_sha=software_commit_sha,
        rule_version=rule_version,
        configuration_digest=configuration_digest,
        app_run_id=app_run_id,
        app_analysis_fingerprint=app_analysis_fingerprint,
        sp_result_fingerprint=sp_result_fingerprint,
        prior_scope_id=prior_scope_id,
        new_session_id=new_session_id,
        retry_scope_id=retry_scope_id,
        linked_session_id=linked_session_id,
    )
    _validate_event_fields(draft)
    computed_id = EVENT_ID_PREFIX + event_fingerprint(draft)
    return replace(draft, event_id=computed_id)


def validate_int_ledger_manifest(manifest: IntLedgerManifest) -> IntLedgerManifest:
    """校验派生 manifest 合同；不读取或重写任何文件。"""

    if manifest.schema_version != LEDGER_MANIFEST_SCHEMA_VERSION:
        raise M1IntError("version_mismatch", "manifest schema_version does not match P4B ledger manifest")
    _require_non_empty("session_id", manifest.session_id)
    _require_non_empty("decision_rule_version", manifest.decision_rule_version)
    _require_hex64("configuration_digest", manifest.configuration_digest)
    _require_git_sha("software_commit_sha", manifest.software_commit_sha)
    _require_hex64("decisions_sha256", manifest.decisions_sha256)
    _require_hex64("events_sha256", manifest.events_sha256)
    _require_non_negative("decision_count", manifest.decision_count)
    _require_non_negative("event_count", manifest.event_count)
    _require_non_negative("last_event_seq", manifest.last_event_seq)
    if manifest.event_count == 0 and manifest.last_event_seq != 0:
        raise M1IntError("invalid_input", "empty event ledger requires last_event_seq=0")
    if manifest.event_count > 0 and manifest.last_event_seq != manifest.event_count:
        raise M1IntError("invalid_input", "last_event_seq must equal event_count for a contiguous ledger")
    if manifest.decision_count == 0 and manifest.current_decision_id is not None:
        raise M1IntError("invalid_input", "empty decision ledger requires current_decision_id=null")
    if manifest.decision_count > 0:
        _require_decision_id(manifest.current_decision_id)
    return manifest


def _validate_event_fields(event: IntLedgerEvent) -> None:
    if event.event_type in _REQUIRED_DECISION_ID_TYPES:
        _require_decision_id(event.decision_id)
    elif event.decision_id is not None:
        _require_decision_id(event.decision_id)

    if event.event_type == "decision_recorded":
        _require_git_sha("software_commit_sha", event.software_commit_sha)
        _require_non_empty("rule_version", event.rule_version)
        _require_hex64("configuration_digest", event.configuration_digest)
    elif event.software_commit_sha is not None:
        _require_git_sha("software_commit_sha", event.software_commit_sha)
    if event.configuration_digest is not None:
        _require_hex64("configuration_digest", event.configuration_digest)
    if event.app_analysis_fingerprint is not None:
        _require_hex64("app_analysis_fingerprint", event.app_analysis_fingerprint)
    if event.sp_result_fingerprint is not None:
        _require_hex64("sp_result_fingerprint", event.sp_result_fingerprint)

    if event.event_type == "operator_override":
        _require_i1_action(event.requested_action)
        _require_non_empty("operator_id", event.operator_id)
        _require_string("note", event.note)
    elif event.event_type == "action_rejected_by_safety":
        _require_i1_action(event.requested_action)
        _require_non_empty("operator_id", event.operator_id)
        if event.note is not None:
            _require_string("note", event.note)
    elif event.event_type == "manual_review_resolved":
        _require_non_empty("operator_id", event.operator_id)
        require_frozen_resolution(event.resolution or "")
    elif event.event_type == "reposition_acknowledged":
        _require_non_empty("operator_id", event.operator_id)
        _require_retry_scope_id(event.prior_scope_id)
        _require_non_empty("new_session_id", event.new_session_id)
    elif event.event_type == "retry_scope_started":
        _require_retry_scope_id(event.retry_scope_id)
        if event.prior_scope_id is not None:
            _require_retry_scope_id(event.prior_scope_id)
    elif event.event_type == "retry_scope_closed":
        _require_retry_scope_id(event.retry_scope_id)
    elif event.event_type == "retry_attempt_linked":
        _require_retry_scope_id(event.retry_scope_id)
        _require_non_empty("linked_session_id", event.linked_session_id)

    if event.resolution is not None and event.event_type != "manual_review_resolved":
        raise M1IntError("invalid_input", "resolution is only valid on manual_review_resolved")
    if event.requested_action is not None and event.event_type not in {
        "operator_override",
        "action_rejected_by_safety",
    }:
        raise M1IntError("invalid_input", "requested_action is not valid on this event_type")


def _resolve_outcome(event_type: str, outcome: str | None) -> str | None:
    default_outcome = _DEFAULT_OUTCOME_BY_TYPE.get(event_type)
    if default_outcome is None:
        if outcome is not None:
            raise M1IntError("invalid_input", "this event_type does not carry an outcome field")
        return None
    if outcome is None:
        return default_outcome
    require_frozen_outcome(outcome)
    if outcome != default_outcome:
        raise M1IntError("invalid_input", "outcome does not match the frozen event_type mapping")
    return outcome


def _require_positive_seq(event_seq: int) -> None:
    if not isinstance(event_seq, int) or isinstance(event_seq, bool) or event_seq < 1:
        raise M1IntError("invalid_input", "event_seq must be an integer >= 1")


def _require_non_negative(field_name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise M1IntError("invalid_input", f"{field_name} must be an integer >= 0")


def _require_non_empty(field_name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise M1IntError("invalid_input", f"{field_name} must be a non-empty string")
    return value


def _require_string(field_name: str, value: str | None) -> str:
    if not isinstance(value, str):
        raise M1IntError("invalid_input", f"{field_name} must be a string")
    return value


def _require_decision_id(decision_id: str | None) -> str:
    value = _require_non_empty("decision_id", decision_id)
    if not DECISION_ID_PATTERN.fullmatch(value):
        raise M1IntError("invalid_input", "decision_id is not a frozen machine decision identity")
    return value


def _require_hex64(field_name: str, value: str | None) -> str:
    text = _require_non_empty(field_name, value)
    if not HEX64_PATTERN.fullmatch(text) or text == "0" * 64:
        raise M1IntError("invalid_input", f"{field_name} must be a non-zero 64-hex digest")
    return text


def _require_git_sha(field_name: str, value: str | None) -> str:
    text = _require_non_empty(field_name, value)
    if not GIT_COMMIT_SHA_PATTERN.fullmatch(text):
        raise M1IntError("invalid_input", f"{field_name} must be a 40-hex commit SHA")
    return text


def _require_i1_action(action: str | None) -> str:
    if not isinstance(action, str) or action not in I1_ACTIONS:
        raise M1IntError("invalid_input", "requested_action must be a frozen I1 action")
    return action


def _require_retry_scope_id(scope_id: str | None) -> str:
    text = _require_non_empty("retry_scope_id", scope_id)
    if not RETRY_SCOPE_ID_PATTERN.fullmatch(text):
        raise M1IntError("invalid_input", "retry_scope_id does not match the frozen identifier form")
    return text


def _require_iso8601_utc(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise M1IntError("invalid_input", "occurred_at_utc must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise M1IntError("invalid_input", "occurred_at_utc is not a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise M1IntError("invalid_input", "occurred_at_utc must include a timezone")
