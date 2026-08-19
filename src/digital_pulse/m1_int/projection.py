"""Scheme B 决策身份与冻结 M1Decision 投影。"""

from __future__ import annotations

from digital_pulse.m1_contracts import DecisionAction, DecisionInputVersions, I1_ACTIONS, M1Decision, RESERVED_FUTURE_ACTIONS

from .errors import M1IntError
from .models import (
    IDENTITY_SCHEMA_VERSION,
    RULE_VERSION,
    DecisionContext,
    DecisionEvaluation,
    authoritative_signal_processing_version,
    sha256_canonical,
)
from .policy import FROZEN_MAX_RETRY_COUNT, I1PolicyConfig, policy_configuration_digest, require_frozen_i1_policy
from .rules import I1RuleEngine


def _signal_processing_version(context: DecisionContext) -> str:
    version = authoritative_signal_processing_version(context)
    if not version:
        raise M1IntError("invalid_input", "authoritative signal_processing_version is missing")
    return version


def build_decision_id(context: DecisionContext, evaluation: DecisionEvaluation, policy: I1PolicyConfig) -> str:
    """方案 B：先求值，再哈希语义输入 + 确定性输出。不含 decision_id / 墙钟 / 软件 SHA。"""

    quality_label = None if context.quality.quality_label is None else context.quality.quality_label.value
    quality_ref = None
    if context.quality.quality_reference is not None:
        quality_ref = {
            "session_id": context.quality.quality_reference.session_id,
            "window_id": context.quality.quality_reference.window_id,
        }
    payload = {
        "canonical_reason_codes": list(evaluation.canonical_reason_codes),
        "completed": context.session.completed,
        "completion_reason": context.session.completion_reason,
        "configuration_digest": policy_configuration_digest(policy),
        "device_state": context.session.device_state,
        "history_fingerprint": evaluation.history_fingerprint,
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "integrity": {
            "frame_loss": context.integrity.frame_loss,
            "sensor_connection_failure": context.integrity.sensor_connection_failure,
            "timestamp_regression": context.integrity.timestamp_regression,
        },
        "matched_rule_id": evaluation.matched_rule_id,
        "max_retry_count": context.history.max_retry_count,
        "operator_stop": context.operator.operator_stop,
        "parameter_status": context.session.parameter_status.value,
        "provenance": {
            "app_analysis_fingerprint": context.provenance.app_analysis_fingerprint,
            "app_run_id": context.provenance.app_run_id,
            "signal_processing_version": _signal_processing_version(context),
            "sp_result_fingerprint": context.provenance.sp_result_fingerprint,
        },
        "quality_label": quality_label,
        "quality_reference": quality_ref,
        "quality_truth": {
            "analysis_allowed": context.quality.analysis_allowed,
            "quality_label": quality_label,
        },
        "raw_persistence_status": context.session.raw_persistence_status.value,
        "recommended_action": evaluation.recommended_action.value,
        "retry_count": context.history.retry_count,
        "retry_scope_id": context.history.retry_scope_id,
        "rule_version": RULE_VERSION,
        "safety": {
            "buffer_overflow": context.safety.buffer_overflow,
            "device_fault": context.safety.device_fault,
            "emergency_stop": context.safety.emergency_stop,
            "hard_overload": context.safety.hard_overload,
            "host_timeout": context.safety.host_timeout,
            "watchdog_timeout": context.safety.watchdog_timeout,
        },
        "session_id": context.session.session_id,
    }
    return "m1-decision-" + sha256_canonical(payload)


def project_m1_decision(
    context: DecisionContext,
    evaluation: DecisionEvaluation,
    policy: I1PolicyConfig,
    *,
    decided_at_utc: str,
) -> M1Decision:
    """由求值结果投影冻结 M1Decision。decided_at_utc 必须由调用方提供。"""

    if not decided_at_utc:
        raise M1IntError("invalid_input", "decided_at_utc must be supplied by caller")
    require_frozen_i1_policy(policy)
    expected = I1RuleEngine().evaluate(context, policy)
    if evaluation != expected:
        raise M1IntError("invalid_input", "evaluation is not bound to this context and policy")
    action = expected.recommended_action
    if action.value in RESERVED_FUTURE_ACTIONS or action.value not in I1_ACTIONS:
        raise M1IntError("invalid_input", f"I1 cannot project action {action.value}")
    digest = expected.configuration_digest
    decision = M1Decision(
        decision_id=build_decision_id(context, expected, policy),
        session_id=context.session.session_id,
        decided_at_utc=decided_at_utc,
        milestone="M1",
        int_level="I1",
        device_state=context.session.device_state,
        quality_reference=context.quality.quality_reference,
        action=action,
        reason_codes=expected.canonical_reason_codes,
        rule_version=RULE_VERSION,
        input_versions=DecisionInputVersions(
            signal_processing_version=_signal_processing_version(context),
            decision_rule_version=RULE_VERSION,
            configuration_digest=digest,
        ),
        retry_count=context.history.retry_count,
        max_retry_count=FROZEN_MAX_RETRY_COUNT,
        operator_override=None,
        outcome=None,
        parameter_status=context.session.parameter_status,
        schema_version="1.0.0",
    )
    decision.validate()
    decision.validate_schema()
    return decision
