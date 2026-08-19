"""纯确定性 I1 规则引擎。无时钟、随机、文件系统、网络或 oracle。"""

from __future__ import annotations

from digital_pulse.m1_contracts import (
    DecisionAction,
    I1_ACTIONS,
    ParameterStatus,
    QualityLabel,
    RawPersistenceStatus,
    RESERVED_FUTURE_ACTIONS,
    SourceType,
)

from .errors import M1IntError
from .models import (
    AUTHORIZED_DEVICE_STATES,
    DECISION_ID_PATTERN,
    GIT_COMMIT_SHA_PATTERN,
    HEX64_PATTERN,
    RULE_VERSION,
    DecisionContext,
    DecisionEvaluation,
    authoritative_signal_processing_version,
    history_fingerprint,
    sha256_canonical,
)
from .policy import I1PolicyConfig, policy_configuration_digest, require_frozen_i1_policy

RETRYABLE_QUALITY = frozenset(
    {
        QualityLabel.WEAK_SIGNAL,
        QualityLabel.UNSTABLE_BASELINE,
        QualityLabel.MOTION_ARTIFACT,
        QualityLabel.INSUFFICIENT_DURATION,
    }
)
CANONICAL_REASONS = (
    "quality_acceptable",
    "weak_signal",
    "no_contact",
    "saturated",
    "unstable_baseline",
    "motion_artifact",
    "insufficient_duration",
    "data_integrity_failure",
    "reference_mismatch",
    "retry_limit_reached",
    "operator_stop",
    "operator_override",
    "device_fault",
    "emergency_stop",
    "hard_overload",
    "host_timeout",
    "watchdog_timeout",
    "manual_review_required",
)
CATEGORY2_FLAGS = (
    ("hard_overload", "hard_overload", "safety.hard_overload"),
    ("host_timeout", "host_timeout", "safety.host_timeout"),
    ("watchdog_timeout", "watchdog_timeout", "safety.watchdog_timeout"),
)


def _reason_tuple(*codes: str) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if code not in CANONICAL_REASONS:
            raise M1IntError("invalid_input", f"illegal canonical reason {code}")
        if code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    if not ordered:
        raise M1IntError("invalid_input", "canonical_reason_codes must not be empty")
    return tuple(ordered)


def _evidence_refs(context: DecisionContext, history_digest: str) -> tuple[str, ...]:
    refs = [f"session:{context.session.session_id}"]
    if context.provenance.app_run_id:
        refs.append(f"app-run:{context.provenance.app_run_id}")
    reference = context.quality.quality_reference
    if reference is not None:
        refs.append(f"quality:{reference.session_id}/{reference.window_id}")
    if context.provenance.sp_result_fingerprint:
        refs.append(f"sp:{context.provenance.sp_result_fingerprint}")
    refs.append(f"history:{history_digest}")
    return tuple(refs)


def _explanation(matched_rule_id: str, reasons: tuple[str, ...], refs: tuple[str, ...]) -> str:
    return f"{matched_rule_id}|{','.join(reasons)}|{'+'.join(refs)}"


def _semantic_input_digest(context: DecisionContext, policy: I1PolicyConfig, history_digest: str) -> str:
    quality_label = None if context.quality.quality_label is None else context.quality.quality_label.value
    quality_ref = None
    if context.quality.quality_reference is not None:
        quality_ref = {
            "session_id": context.quality.quality_reference.session_id,
            "window_id": context.quality.quality_reference.window_id,
        }
    payload = {
        "analysis_allowed": context.quality.analysis_allowed,
        "completion_reason": context.session.completion_reason,
        "device_state": context.session.device_state,
        "history_fingerprint": history_digest,
        "integrity": {
            "frame_loss": context.integrity.frame_loss,
            "sensor_connection_failure": context.integrity.sensor_connection_failure,
            "timestamp_regression": context.integrity.timestamp_regression,
        },
        "max_retry_count": context.history.max_retry_count,
        "operator_stop": context.operator.operator_stop,
        "parameter_status": context.session.parameter_status.value,
        "policy_digest": policy_configuration_digest(policy),
        "provenance": {
            "app_analysis_fingerprint": context.provenance.app_analysis_fingerprint,
            "app_run_id": context.provenance.app_run_id,
            "signal_processing_version": authoritative_signal_processing_version(context),
            "sp_result_fingerprint": context.provenance.sp_result_fingerprint,
        },
        "quality_label": quality_label,
        "quality_reference": quality_ref,
        "raw_persistence_status": context.session.raw_persistence_status.value,
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
    return sha256_canonical(payload)


def _require_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise M1IntError("invalid_input", f"{field_name} must be a boolean")


def _require_optional_hex64(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if not HEX64_PATTERN.fullmatch(value):
        raise M1IntError("invalid_input", f"{field_name} must be 64 lowercase hex chars")


def _validate_context(context: DecisionContext, policy: I1PolicyConfig) -> None:
    if not isinstance(context.session.source_type, SourceType):
        raise M1IntError("invalid_input", "source_type must be a frozen SourceType")
    if not isinstance(context.session.parameter_status, ParameterStatus):
        raise M1IntError("invalid_input", "parameter_status must be a frozen ParameterStatus")
    if not isinstance(context.session.raw_persistence_status, RawPersistenceStatus):
        raise M1IntError("invalid_input", "raw_persistence_status must be a frozen RawPersistenceStatus")
    _require_bool(context.session.completed, "completed")
    _require_bool(context.safety.emergency_stop, "emergency_stop")
    _require_bool(context.safety.device_fault, "device_fault")
    _require_bool(context.safety.hard_overload, "hard_overload")
    _require_bool(context.safety.host_timeout, "host_timeout")
    _require_bool(context.safety.watchdog_timeout, "watchdog_timeout")
    _require_bool(context.safety.buffer_overflow, "buffer_overflow")
    _require_bool(context.integrity.sensor_connection_failure, "sensor_connection_failure")
    _require_bool(context.integrity.frame_loss, "frame_loss")
    _require_bool(context.integrity.timestamp_regression, "timestamp_regression")
    _require_bool(context.operator.operator_stop, "operator_stop")
    _require_bool(context.history.reposition_acknowledged, "reposition_acknowledged")
    if context.quality.analysis_allowed is not None:
        _require_bool(context.quality.analysis_allowed, "analysis_allowed")
    if context.quality.quality_label is not None and not isinstance(context.quality.quality_label, QualityLabel):
        raise M1IntError("invalid_input", "quality_label must be a frozen QualityLabel")

    if not context.session.session_id.strip():
        raise M1IntError("invalid_input", "session_id is required")
    if context.session.device_state not in AUTHORIZED_DEVICE_STATES:
        raise M1IntError("unsupported_device_state", "unknown or unauthorized device_state")
    if context.safety.device_fault or context.safety.buffer_overflow:
        if context.session.device_state != "FAULT":
            raise M1IntError(
                "invalid_input",
                "device_fault/buffer_overflow facts require device_state=FAULT",
            )
    if not context.history.retry_scope_id.strip():
        raise M1IntError("invalid_retry_state", "retry_scope_id is required")
    if context.history.retry_count < 0:
        raise M1IntError("invalid_retry_state", "retry_count must be >= 0")
    if context.history.retry_count > policy.max_retry_count:
        raise M1IntError("invalid_retry_state", "retry_count cannot exceed max_retry_count")
    if context.history.max_retry_count != policy.max_retry_count:
        raise M1IntError("invalid_retry_state", "history.max_retry_count must match policy")
    if len(context.history.prior_decision_ids) != len(context.history.prior_actions):
        raise M1IntError("invalid_retry_state", "prior_decision_ids and prior_actions length mismatch")
    if context.history.reposition_acknowledged and context.history.retry_count != 0:
        raise M1IntError("invalid_retry_state", "reposition_acknowledged cannot coexist with retry_count>0")
    retry_issued = 0
    seen_decision_ids: set[str] = set()
    for action, decision_id in zip(
        context.history.prior_actions,
        context.history.prior_decision_ids,
        strict=True,
    ):
        if action not in I1_ACTIONS:
            raise M1IntError("invalid_retry_state", f"illegal prior action {action}")
        if action == DecisionAction.RETRY_SAME_POSITION.value:
            retry_issued += 1
        if not DECISION_ID_PATTERN.fullmatch(decision_id):
            raise M1IntError("invalid_retry_state", "prior decision_id is malformed")
        if decision_id in seen_decision_ids:
            raise M1IntError("invalid_retry_state", "prior decision_id is duplicated")
        seen_decision_ids.add(decision_id)
    if retry_issued != context.history.retry_count:
        raise M1IntError(
            "invalid_retry_state",
            "retry_count must equal prior retry_same_position count",
        )

    reference = context.quality.quality_reference
    if reference is not None:
        if not reference.session_id.strip() or not reference.window_id.strip():
            raise M1IntError("invalid_input", "quality_reference identifiers must be non-empty")
        if reference.session_id != context.session.session_id:
            raise M1IntError("provenance_mismatch", "quality_reference.session_id must match session_id")
    if context.quality.quality_label is not None and reference is None:
        raise M1IntError("invalid_input", "quality_reference is required when quality_label exists")
    if context.quality.quality_label is None and reference is not None:
        raise M1IntError("invalid_input", "quality_label is required when quality_reference exists")
    if context.quality.quality_label is not None and context.quality.analysis_allowed is None:
        raise M1IntError("invalid_input", "analysis_allowed is required when quality_label exists")
    if context.quality.quality_label is QualityLabel.ACCEPTABLE and context.quality.analysis_allowed is not True:
        raise M1IntError("invalid_input", "ACCEPT requires analysis_allowed=True")

    if not GIT_COMMIT_SHA_PATTERN.fullmatch(context.provenance.software_commit_sha):
        raise M1IntError("invalid_input", "software_commit_sha must be 40 lowercase hex chars")
    app_run_id = context.provenance.app_run_id
    if app_run_id is not None and not app_run_id.strip():
        raise M1IntError("invalid_input", "app_run_id must not be whitespace")
    _require_optional_hex64(context.provenance.app_analysis_fingerprint, "app_analysis_fingerprint")
    _require_optional_hex64(context.provenance.sp_result_fingerprint, "sp_result_fingerprint")
    if app_run_id:
        if not context.provenance.run_signal_processing_version or not context.provenance.run_signal_processing_version.strip():
            raise M1IntError("invalid_input", "run_signal_processing_version is required when app_run_id exists")
        if not context.provenance.app_analysis_fingerprint:
            raise M1IntError("invalid_input", "app_analysis_fingerprint is required when app_run_id exists")
        if not context.provenance.sp_result_fingerprint:
            raise M1IntError("invalid_input", "sp_result_fingerprint is required when app_run_id exists")
        session_version = context.provenance.session_signal_processing_version
        if session_version and session_version != context.provenance.run_signal_processing_version:
            raise M1IntError("provenance_mismatch", "run and session signal_processing_version conflict")
    else:
        session_version = context.provenance.session_signal_processing_version
        if not session_version or not session_version.strip():
            raise M1IntError("invalid_input", "session_signal_processing_version is required when app_run_id is absent")
        if context.quality.quality_label is not None:
            raise M1IntError("provenance_mismatch", "quality path requires exact APP-run provenance")


def _build_evaluation(
    context: DecisionContext,
    policy: I1PolicyConfig,
    *,
    action: DecisionAction,
    reasons: tuple[str, ...],
    matched_rule_id: str,
    rule_priority: int,
    history_digest: str,
    input_digest: str,
) -> DecisionEvaluation:
    if action.value in RESERVED_FUTURE_ACTIONS or action.value not in I1_ACTIONS:
        raise M1IntError("invalid_input", f"I1 cannot emit action {action.value}")
    refs = _evidence_refs(context, history_digest)
    return DecisionEvaluation(
        recommended_action=action,
        canonical_reason_codes=reasons,
        matched_rule_id=matched_rule_id,
        rule_priority=rule_priority,
        semantic_input_digest=input_digest,
        rule_version=RULE_VERSION,
        configuration_digest=policy_configuration_digest(policy),
        evidence_refs=refs,
        human_readable_explanation=_explanation(matched_rule_id, reasons, refs),
        history_fingerprint=history_digest,
    )


class I1RuleEngine:
    """DecisionContext + I1PolicyConfig → DecisionEvaluation。"""

    def evaluate(self, context: DecisionContext, policy: I1PolicyConfig) -> DecisionEvaluation:
        require_frozen_i1_policy(policy)
        _validate_context(context, policy)
        history_digest = history_fingerprint(context.history)
        input_digest = _semantic_input_digest(context, policy, history_digest)

        def emit(action: DecisionAction, reasons: tuple[str, ...], rule_id: str, priority: int) -> DecisionEvaluation:
            return _build_evaluation(
                context,
                policy,
                action=action,
                reasons=reasons,
                matched_rule_id=rule_id,
                rule_priority=priority,
                history_digest=history_digest,
                input_digest=input_digest,
            )

        completion = context.session.completion_reason
        emergency = context.safety.emergency_stop or completion == "abort_and_release"
        if emergency:
            return emit(
                DecisionAction.ABORT_AND_RELEASE,
                _reason_tuple("emergency_stop"),
                "safety.emergency_stop",
                1,
            )

        category2 = [
            (reason, rule_id)
            for attr, reason, rule_id in CATEGORY2_FLAGS
            if getattr(context.safety, attr)
        ]
        if category2:
            reasons = _reason_tuple(*(item[0] for item in category2))
            return emit(DecisionAction.ABORT_AND_RELEASE, reasons, category2[0][1], 1)

        if context.integrity.sensor_connection_failure:
            return emit(
                DecisionAction.STOP,
                _reason_tuple("data_integrity_failure"),
                "integrity.sensor_disconnected",
                1,
            )

        classified_device_fault = (
            context.session.device_state == "FAULT"
            and not context.integrity.sensor_connection_failure
            and (
                context.safety.device_fault
                or completion == "device_fault"
                or context.safety.buffer_overflow
            )
        )
        if classified_device_fault:
            return emit(
                DecisionAction.ABORT_AND_RELEASE,
                _reason_tuple("device_fault"),
                "safety.device_fault",
                1,
            )

        if context.session.device_state in {"FAULT", "SAFE_HOLD"}:
            raise M1IntError(
                "unsupported_device_state",
                "unclassified FAULT/SAFE_HOLD cannot emit M1Decision",
            )

        if context.operator.operator_stop:
            return emit(DecisionAction.STOP, _reason_tuple("operator_stop"), "operator.operator_stop", 2)

        raw_status = context.session.raw_persistence_status
        if raw_status is RawPersistenceStatus.FAILED:
            return emit(
                DecisionAction.STOP,
                _reason_tuple("data_integrity_failure"),
                "integrity.raw_persistence_failure",
                3,
            )
        if raw_status in {RawPersistenceStatus.PARTIAL, RawPersistenceStatus.NOT_STARTED}:
            raise M1IntError(
                "invalid_input",
                "raw_persistence_status partial/not_started has no frozen I1-pre action mapping",
            )
        if context.integrity.frame_loss:
            return emit(
                DecisionAction.STOP,
                _reason_tuple("data_integrity_failure"),
                "integrity.frame_loss",
                3,
            )
        if context.integrity.timestamp_regression:
            return emit(
                DecisionAction.STOP,
                _reason_tuple("data_integrity_failure"),
                "integrity.timestamp_regression",
                3,
            )

        label = context.quality.quality_label
        if label is None:
            raise M1IntError("invalid_input", "quality_label is required after safety/integrity gates")
        if context.quality.quality_reference is None:
            raise M1IntError("invalid_input", "quality_reference is required when quality_label exists")

        if label is QualityLabel.ACCEPTABLE:
            return emit(
                DecisionAction.ACCEPT,
                _reason_tuple("quality_acceptable"),
                "quality.acceptable",
                6,
            )
        if label in RETRYABLE_QUALITY:
            if context.history.retry_count < policy.max_retry_count:
                return emit(
                    DecisionAction.RETRY_SAME_POSITION,
                    _reason_tuple(label.value),
                    f"quality.{label.value}.retry",
                    5,
                )
            return emit(
                DecisionAction.REPOSITION,
                _reason_tuple(label.value, "retry_limit_reached"),
                f"quality.{label.value}.exhausted",
                5,
            )
        if label is QualityLabel.NO_CONTACT:
            return emit(DecisionAction.REPOSITION, _reason_tuple("no_contact"), "quality.no_contact", 4)
        if label is QualityLabel.SATURATED:
            return emit(DecisionAction.STOP, _reason_tuple("saturated"), "quality.saturated", 4)
        if label is QualityLabel.DATA_INTEGRITY_FAILURE:
            return emit(
                DecisionAction.STOP,
                _reason_tuple("data_integrity_failure"),
                "quality.data_integrity_failure",
                4,
            )
        if label is QualityLabel.REFERENCE_MISMATCH:
            return emit(
                DecisionAction.MANUAL_REVIEW,
                _reason_tuple("reference_mismatch"),
                "quality.reference_mismatch",
                4,
            )
        if label is QualityLabel.MANUAL_REVIEW_REQUIRED:
            return emit(
                DecisionAction.MANUAL_REVIEW,
                _reason_tuple("manual_review_required"),
                "quality.manual_review_required",
                4,
            )
        raise M1IntError("invalid_input", f"unhandled quality_label {label.value}")
