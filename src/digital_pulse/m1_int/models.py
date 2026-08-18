"""P4A 不可变结构化事实模型。不是新的 P0 跨层合同。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from digital_pulse.m1_contracts import (
    DecisionAction,
    ParameterStatus,
    QualityLabel,
    QualityReference,
    RawPersistenceStatus,
    SourceType,
)

from .errors import M1IntError

RULE_VERSION = "i1-pre-0.1.0"
IDENTITY_SCHEMA_VERSION = "i1-decision-identity-v1"


def dumps_canonical(payload: Mapping[str, Any]) -> str:
    """UTF-8 稳定 JSON：排序键、紧凑分隔符、禁止 NaN/Infinity。"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_canonical(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(dumps_canonical(payload).encode("utf-8")).hexdigest()
    if digest == "0" * 64:
        raise M1IntError("invalid_input", "canonical digest must not be all zeros")
    return digest


@dataclass(frozen=True, slots=True)
class SessionFacts:
    session_id: str
    source_type: SourceType
    completed: bool
    completion_reason: str | None
    device_state: str
    raw_persistence_status: RawPersistenceStatus
    parameter_status: ParameterStatus
    side: str | None = None
    site: str | None = None
    probe_id: str | None = None


@dataclass(frozen=True, slots=True)
class SafetyFacts:
    emergency_stop: bool = False
    device_fault: bool = False
    hard_overload: bool = False
    host_timeout: bool = False
    watchdog_timeout: bool = False
    buffer_overflow: bool = False


@dataclass(frozen=True, slots=True)
class IntegrityFacts:
    sensor_connection_failure: bool = False
    frame_loss: bool = False
    timestamp_regression: bool = False


@dataclass(frozen=True, slots=True)
class QualityFacts:
    quality_label: QualityLabel | None
    quality_reference: QualityReference | None
    analysis_allowed: bool | None


@dataclass(frozen=True, slots=True)
class HistoryFacts:
    retry_scope_id: str
    retry_count: int
    max_retry_count: int
    prior_decision_ids: tuple[str, ...]
    prior_actions: tuple[str, ...]
    reposition_acknowledged: bool


@dataclass(frozen=True, slots=True)
class OperatorFacts:
    operator_stop: bool = False


@dataclass(frozen=True, slots=True)
class DecisionSourceProvenance:
    app_run_id: str | None
    app_analysis_fingerprint: str | None
    sp_result_fingerprint: str | None
    run_signal_processing_version: str | None
    session_signal_processing_version: str | None
    software_commit_sha: str


@dataclass(frozen=True, slots=True)
class DecisionContext:
    session: SessionFacts
    safety: SafetyFacts
    integrity: IntegrityFacts
    quality: QualityFacts
    history: HistoryFacts
    operator: OperatorFacts
    provenance: DecisionSourceProvenance


@dataclass(frozen=True, slots=True)
class DecisionEvaluation:
    recommended_action: DecisionAction
    canonical_reason_codes: tuple[str, ...]
    matched_rule_id: str
    rule_priority: int
    semantic_input_digest: str
    rule_version: str
    configuration_digest: str
    evidence_refs: tuple[str, ...]
    human_readable_explanation: str
    history_fingerprint: str


def history_fingerprint(history: HistoryFacts) -> str:
    """由显式 HistoryFacts 计算确定性指纹；P4A 不重建 ledger。"""

    payload = {
        "max_retry_count": history.max_retry_count,
        "prior_actions": list(history.prior_actions),
        "prior_decision_ids": list(history.prior_decision_ids),
        "reposition_acknowledged": history.reposition_acknowledged,
        "retry_count": history.retry_count,
        "retry_scope_id": history.retry_scope_id,
    }
    return sha256_canonical(payload)
