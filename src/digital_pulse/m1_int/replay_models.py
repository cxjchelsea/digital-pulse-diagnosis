"""P4B-D typed immutable replay models. No I/O, no rule evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from digital_pulse.m1_contracts import M1Decision
from digital_pulse.m1_int.errors import M1IntError
from digital_pulse.m1_int.ledger_models import IntLedgerEvent

_SNAPSHOT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Verified in-memory ledger snapshot. Construct only via internal factory."""

    session_id: str
    machine_decisions: tuple[M1Decision, ...]
    events: tuple[IntLedgerEvent, ...]
    ledger_schema_version: str
    manifest_schema_version: str
    decisions_sha256: str
    events_sha256: str
    last_event_seq: int
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _SNAPSHOT_TOKEN:
            raise M1IntError(
                "invalid_input",
                "LedgerSnapshot must be constructed via verified snapshot acquisition",
            )


@dataclass(frozen=True, slots=True)
class ReconstructedDecisionView:
    """Folded decision view. machine_action is persisted; replayed_action is derived."""

    decision_id: str
    machine_action: str
    replayed_action: str
    outcome: str | None
    awaiting_operator: bool
    override_facts: tuple[IntLedgerEvent, ...]
    rejection_facts: tuple[IntLedgerEvent, ...]
    manual_review_resolution: str | None
    completed: bool
    machine_reason_codes: tuple[str, ...]
    rule_version: str
    parameter_status: str
    provenance: Mapping[str, str | None]
    derived_action_at_apply: str | None


@dataclass(frozen=True, slots=True)
class LedgerReplayResult:
    """Deterministic replay result for one session ledger."""

    session_id: str
    machine_decisions: tuple[M1Decision, ...]
    events: tuple[IntLedgerEvent, ...]
    views: tuple[ReconstructedDecisionView, ...]
    integrity_status: str
    last_event_seq: int
    replay_fingerprint: str
    p4c_facts: tuple[IntLedgerEvent, ...]


def make_verified_snapshot(
    *,
    session_id: str,
    machine_decisions: tuple[M1Decision, ...],
    events: tuple[IntLedgerEvent, ...],
    ledger_schema_version: str,
    manifest_schema_version: str,
    decisions_sha256: str,
    events_sha256: str,
    last_event_seq: int,
) -> LedgerSnapshot:
    """Internal factory used after lock-protected verification."""

    return LedgerSnapshot(
        session_id=session_id,
        machine_decisions=machine_decisions,
        events=events,
        ledger_schema_version=ledger_schema_version,
        manifest_schema_version=manifest_schema_version,
        decisions_sha256=decisions_sha256,
        events_sha256=events_sha256,
        last_event_seq=last_event_seq,
        _token=_SNAPSHOT_TOKEN,
    )


def freeze_provenance(values: Mapping[str, str | None]) -> Mapping[str, str | None]:
    return MappingProxyType(dict(values))


def is_verified_snapshot(snapshot: Any) -> bool:
    return isinstance(snapshot, LedgerSnapshot) and snapshot._token is _SNAPSHOT_TOKEN
