"""P4B-C event helpers. Typed persist only; no public generic append."""

from __future__ import annotations

from typing import Any

from digital_pulse.m1_int.errors import M1IntError
from digital_pulse.m1_int.ledger_models import (
    IntLedgerEvent,
    build_int_ledger_event,
    canonical_event_payload,
)
from digital_pulse.m1_int.models import DecisionSourceProvenance

_PROVENANCE_KEYS = (
    "software_commit_sha",
    "app_run_id",
    "app_analysis_fingerprint",
    "sp_result_fingerprint",
)


def event_business_key(event: IntLedgerEvent) -> tuple[Any, ...]:
    if event.event_type == "retry_scope_started" or event.event_type == "retry_scope_closed":
        return (event.event_type, event.retry_scope_id)
    if event.event_type == "retry_attempt_linked":
        return (event.event_type, event.decision_id, event.retry_scope_id)
    return (event.event_type, event.decision_id)


def identity_without_seq(event: IntLedgerEvent) -> dict[str, Any]:
    payload = canonical_event_payload(event)
    payload.pop("event_seq", None)
    return payload


def provenance_tuple(event: IntLedgerEvent | dict[str, Any]) -> tuple[Any, ...]:
    getter = event.get if isinstance(event, dict) else lambda key: getattr(event, key)
    return tuple(getter(key) for key in _PROVENANCE_KEYS)


def build_typed_event(
    *,
    event_seq: int,
    event_type: str,
    session_id: str,
    occurred_at: str,
    provenance: DecisionSourceProvenance,
    decision_id: str | None = None,
    requested_action: str | None = None,
    operator_id: str | None = None,
    note: str | None = None,
    resolution: str | None = None,
    outcome: str | None = None,
    rule_version: str | None = None,
    configuration_digest: str | None = None,
    prior_scope_id: str | None = None,
    new_session_id: str | None = None,
    retry_scope_id: str | None = None,
    linked_session_id: str | None = None,
) -> IntLedgerEvent:
    return build_int_ledger_event(
        event_seq=event_seq,
        event_type=event_type,
        session_id=session_id,
        occurred_at_utc=occurred_at,
        decision_id=decision_id,
        requested_action=requested_action,
        operator_id=operator_id,
        note=note,
        resolution=resolution,
        outcome=outcome,
        software_commit_sha=provenance.software_commit_sha,
        rule_version=rule_version,
        configuration_digest=configuration_digest,
        app_run_id=provenance.app_run_id,
        app_analysis_fingerprint=provenance.app_analysis_fingerprint,
        sp_result_fingerprint=provenance.sp_result_fingerprint,
        prior_scope_id=prior_scope_id,
        new_session_id=new_session_id,
        retry_scope_id=retry_scope_id,
        linked_session_id=linked_session_id,
    )


def require_operator_identity(operator_id: str | None) -> str:
    if not isinstance(operator_id, str) or not operator_id.strip():
        raise M1IntError("invalid_input", "operator_id must be a non-empty unavailable-safe identity")
    return operator_id
