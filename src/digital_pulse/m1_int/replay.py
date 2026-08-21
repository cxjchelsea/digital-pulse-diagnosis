"""P4B-D deterministic ledger fold. Pure function over a verified snapshot."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from digital_pulse.m1_contracts import DecisionAction, M1Decision
from digital_pulse.m1_int.errors import M1IntError
from digital_pulse.m1_int.ledger_models import (
    FROZEN_EVENT_TYPES,
    LEDGER_MANIFEST_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    IntLedgerEvent,
)
from digital_pulse.m1_int.models import sha256_canonical
from digital_pulse.m1_int.override_safety import OverrideClassification, classify_override
from digital_pulse.m1_int.replay_models import (
    LedgerReplayResult,
    LedgerSnapshot,
    ReconstructedDecisionView,
    freeze_provenance,
    is_verified_snapshot,
)

P4C_OWNED_EVENT_TYPES = frozenset(
    {
        "reposition_acknowledged",
        "retry_scope_started",
        "retry_scope_closed",
        "retry_attempt_linked",
    }
)
_DECISION_REFERENCING_TYPES = FROZEN_EVENT_TYPES - {"retry_scope_started", "retry_scope_closed"}


@dataclass
class _MutableView:
    decision_id: str
    machine: M1Decision
    machine_action: str
    replayed_action: str
    outcome: str | None
    awaiting_operator: bool
    override_facts: list[IntLedgerEvent]
    rejection_facts: list[IntLedgerEvent]
    manual_review_resolution: str | None
    completed: bool
    provenance: dict[str, str | None]
    derived_action_at_apply: str | None


def fold_ledger_snapshot(snapshot: LedgerSnapshot) -> LedgerReplayResult:
    """Pure deterministic fold. Caller must supply a verified snapshot."""

    if not is_verified_snapshot(snapshot):
        raise M1IntError(
            "invalid_input",
            "fold_ledger_snapshot requires a verified LedgerSnapshot",
        )
    machines = {item.decision_id: item for item in snapshot.machine_decisions}
    views: dict[str, _MutableView] = {}
    p4c_facts: list[IntLedgerEvent] = []
    for event in snapshot.events:
        _fold_event(event, machines, views, p4c_facts)
    if set(machines) - set(views):
        raise M1IntError("decision_record_mismatch", "machine decision has no decision_recorded")
    frozen_views = tuple(_freeze_view(views[item.decision_id]) for item in snapshot.machine_decisions)
    return LedgerReplayResult(
        session_id=snapshot.session_id,
        machine_decisions=snapshot.machine_decisions,
        events=snapshot.events,
        views=frozen_views,
        integrity_status="trusted",
        last_event_seq=snapshot.last_event_seq,
        replay_fingerprint=_replay_fingerprint(snapshot),
        p4c_facts=tuple(p4c_facts),
    )


def _fold_event(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
    p4c_facts: list[IntLedgerEvent],
) -> None:
    if event.event_type not in FROZEN_EVENT_TYPES:
        raise M1IntError("unsupported_event_type", "fold received an unknown event_type")
    if event.ledger_schema_version != LEDGER_SCHEMA_VERSION:
        raise M1IntError("unsupported_schema_version", "fold received an unsupported ledger schema")
    if event.event_type in P4C_OWNED_EVENT_TYPES:
        if event.event_type in _DECISION_REFERENCING_TYPES:
            _require_view(event, views)
        p4c_facts.append(event)
        return
    handler = _FOLD_HANDLERS[event.event_type]
    handler(event, machines, views)


def _fold_decision_recorded(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    decision_id = _require_decision_id(event)
    if decision_id in views:
        raise M1IntError("decision_record_mismatch", "decision_recorded is duplicated")
    machine = machines.get(decision_id)
    if machine is None:
        raise M1IntError("dangling_decision_reference", "decision_recorded references an unknown decision")
    action = _machine_action(machine)
    views[decision_id] = _MutableView(
        decision_id=decision_id,
        machine=machine,
        machine_action=action,
        replayed_action=action,
        outcome=None,
        awaiting_operator=False,
        override_facts=[],
        rejection_facts=[],
        manual_review_resolution=None,
        completed=False,
        provenance={
            "software_commit_sha": event.software_commit_sha,
            "app_run_id": event.app_run_id,
            "app_analysis_fingerprint": event.app_analysis_fingerprint,
            "sp_result_fingerprint": event.sp_result_fingerprint,
            "rule_version": event.rule_version,
            "configuration_digest": event.configuration_digest,
        },
        derived_action_at_apply=None,
    )


def _fold_operator_override(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_open_view(event, views)
    if view.override_facts:
        raise M1IntError("lifecycle_conflict", "operator_override already exists for this decision")
    requested = event.requested_action or ""
    classification = classify_override(view.machine_action, requested)
    if classification is OverrideClassification.REJECTED_BY_SAFETY:
        raise M1IntError("lifecycle_conflict", "rejected override persisted as operator_override")
    if classification not in {
        OverrideClassification.ALLOWED,
        OverrideClassification.IDEMPOTENT_SAME_ACTION,
    }:
        raise M1IntError("lifecycle_conflict", "operator_override classification is not foldable")
    view.replayed_action = requested
    view.override_facts.append(event)


def _fold_rejected(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_view(event, views)
    view.rejection_facts.append(event)


def _fold_applied(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_open_view(event, views)
    if view.outcome == "applied":
        raise M1IntError("lifecycle_conflict", "action_applied is repeated")
    view.derived_action_at_apply = view.replayed_action
    view.outcome = "applied"


def _fold_awaiting(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_open_view(event, views)
    view.awaiting_operator = True
    if view.outcome is None:
        view.outcome = "awaiting_operator"


def _fold_manual_review(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_open_view(event, views)
    if not view.awaiting_operator:
        raise M1IntError("lifecycle_conflict", "manual_review_resolved requires an awaiting decision")
    if view.manual_review_resolution is not None:
        raise M1IntError("lifecycle_conflict", "manual_review_resolved already exists")
    resolution = event.resolution
    view.manual_review_resolution = resolution
    if resolution == "terminate_stop":
        view.awaiting_operator = False


def _fold_completed(
    event: IntLedgerEvent,
    machines: dict[str, M1Decision],
    views: dict[str, _MutableView],
) -> None:
    del machines
    view = _require_open_view(event, views)
    view.completed = True
    view.outcome = "completed"
    view.awaiting_operator = False


_FOLD_HANDLERS = {
    "decision_recorded": _fold_decision_recorded,
    "operator_override": _fold_operator_override,
    "action_rejected_by_safety": _fold_rejected,
    "action_applied": _fold_applied,
    "awaiting_operator": _fold_awaiting,
    "manual_review_resolved": _fold_manual_review,
    "decision_completed": _fold_completed,
}


def _require_decision_id(event: IntLedgerEvent) -> str:
    if not event.decision_id:
        raise M1IntError("dangling_decision_reference", "event is missing decision_id")
    return event.decision_id


def _require_view(event: IntLedgerEvent, views: dict[str, _MutableView]) -> _MutableView:
    decision_id = _require_decision_id(event)
    view = views.get(decision_id)
    if view is None:
        raise M1IntError("dangling_decision_reference", "event references an unknown decision")
    return view


def _require_open_view(event: IntLedgerEvent, views: dict[str, _MutableView]) -> _MutableView:
    view = _require_view(event, views)
    if view.completed:
        raise M1IntError("lifecycle_conflict", "decision_completed is terminal for reconstructed lifecycle")
    return view


def _machine_action(decision: M1Decision) -> str:
    action = decision.action
    return action.value if isinstance(action, DecisionAction) else str(action)


def _freeze_view(view: _MutableView) -> ReconstructedDecisionView:
    status = view.machine.parameter_status
    status_text = status.value if hasattr(status, "value") else str(status)
    return ReconstructedDecisionView(
        decision_id=view.decision_id,
        machine_action=view.machine_action,
        replayed_action=view.replayed_action,
        outcome=view.outcome,
        awaiting_operator=view.awaiting_operator,
        override_facts=tuple(view.override_facts),
        rejection_facts=tuple(view.rejection_facts),
        manual_review_resolution=view.manual_review_resolution,
        completed=view.completed,
        machine_reason_codes=tuple(view.machine.reason_codes),
        rule_version=view.machine.rule_version,
        parameter_status=status_text,
        provenance=freeze_provenance(view.provenance),
        derived_action_at_apply=view.derived_action_at_apply,
    )


def _replay_fingerprint(snapshot: LedgerSnapshot) -> str:
    payload: dict[str, Any] = {
        "events": [_event_record(item) for item in snapshot.events],
        "ledger_schema_version": snapshot.ledger_schema_version or LEDGER_SCHEMA_VERSION,
        "machine_decisions": [item.to_dict() for item in snapshot.machine_decisions],
        "manifest_schema_version": snapshot.manifest_schema_version or LEDGER_MANIFEST_SCHEMA_VERSION,
        "session_id": snapshot.session_id,
    }
    return sha256_canonical(payload)


def _event_record(event: IntLedgerEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in fields(event):
        value = getattr(event, item.name)
        if value is not None:
            payload[item.name] = value
    return payload
