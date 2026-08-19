"""P4A 测试用 DecisionContext 工厂。不包含决策算法。"""

from __future__ import annotations

from digital_pulse.m1_contracts import (
    ParameterStatus,
    QualityLabel,
    QualityReference,
    RawPersistenceStatus,
    SourceType,
)
from digital_pulse.m1_int import (
    DecisionContext,
    DecisionSourceProvenance,
    HistoryFacts,
    IntegrityFacts,
    OperatorFacts,
    QualityFacts,
    SafetyFacts,
    SessionFacts,
)

SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SP_VERSION = "0.4.0-p2d"
APP_RUN_ID = "run-p4a-001"
APP_FINGERPRINT = "b" * 64
SP_FINGERPRINT = "c" * 64
SESSION_ID = "session-p4a-001"
WINDOW_ID = "window-0001"
RETRY_SCOPE_ID = "retry-scope-001"
POLICY_DECIDED_AT_A = "2026-01-01T00:00:00Z"
POLICY_DECIDED_AT_B = "2026-06-01T12:34:56Z"


def consistent_retry_history(retry_count: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """构造与 retry_count 自洽的 prior retry_same_position 历史。"""

    if retry_count <= 0:
        return (), ()
    prior_actions = tuple("retry_same_position" for _ in range(retry_count))
    prior_decision_ids = tuple(f"m1-decision-{'a' * 62}{index:02x}" for index in range(retry_count))
    return prior_actions, prior_decision_ids


def make_context(
    *,
    session_id: str = SESSION_ID,
    source_type: SourceType = SourceType.SIMULATOR,
    completed: bool = True,
    completion_reason: str | None = "complete",
    device_state: str = "ACQUIRE",
    raw_persistence_status: RawPersistenceStatus = RawPersistenceStatus.OK,
    parameter_status: ParameterStatus = ParameterStatus.PENDING_H1_CALIBRATION,
    emergency_stop: bool = False,
    device_fault: bool = False,
    hard_overload: bool = False,
    host_timeout: bool = False,
    watchdog_timeout: bool = False,
    buffer_overflow: bool = False,
    sensor_connection_failure: bool = False,
    frame_loss: bool = False,
    timestamp_regression: bool = False,
    quality_label: QualityLabel | None = QualityLabel.ACCEPTABLE,
    window_id: str | None = WINDOW_ID,
    analysis_allowed: bool | None = True,
    retry_count: int = 0,
    max_retry_count: int = 2,
    retry_scope_id: str = RETRY_SCOPE_ID,
    prior_decision_ids: tuple[str, ...] | None = None,
    prior_actions: tuple[str, ...] | None = None,
    reposition_acknowledged: bool = False,
    operator_stop: bool = False,
    app_run_id: str | None = APP_RUN_ID,
    app_analysis_fingerprint: str | None = APP_FINGERPRINT,
    sp_result_fingerprint: str | None = SP_FINGERPRINT,
    run_signal_processing_version: str | None = SP_VERSION,
    session_signal_processing_version: str | None = SP_VERSION,
    software_commit_sha: str = SOFTWARE_SHA,
) -> DecisionContext:
    """构造显式结构化 DecisionContext；调用方覆盖字段以表达场景。"""

    if prior_actions is None and prior_decision_ids is None:
        prior_actions, prior_decision_ids = consistent_retry_history(retry_count)
    elif prior_actions is None:
        prior_actions = ()
    elif prior_decision_ids is None:
        prior_decision_ids = ()

    quality_reference = None
    if quality_label is not None and window_id is not None:
        quality_reference = QualityReference(session_id=session_id, window_id=window_id)
    return DecisionContext(
        session=SessionFacts(
            session_id=session_id,
            source_type=source_type,
            completed=completed,
            completion_reason=completion_reason,
            device_state=device_state,
            raw_persistence_status=raw_persistence_status,
            parameter_status=parameter_status,
        ),
        safety=SafetyFacts(
            emergency_stop=emergency_stop,
            device_fault=device_fault,
            hard_overload=hard_overload,
            host_timeout=host_timeout,
            watchdog_timeout=watchdog_timeout,
            buffer_overflow=buffer_overflow,
        ),
        integrity=IntegrityFacts(
            sensor_connection_failure=sensor_connection_failure,
            frame_loss=frame_loss,
            timestamp_regression=timestamp_regression,
        ),
        quality=QualityFacts(
            quality_label=quality_label,
            quality_reference=quality_reference,
            analysis_allowed=analysis_allowed,
        ),
        history=HistoryFacts(
            retry_scope_id=retry_scope_id,
            retry_count=retry_count,
            max_retry_count=max_retry_count,
            prior_decision_ids=prior_decision_ids,
            prior_actions=prior_actions,
            reposition_acknowledged=reposition_acknowledged,
        ),
        operator=OperatorFacts(operator_stop=operator_stop),
        provenance=DecisionSourceProvenance(
            app_run_id=app_run_id,
            app_analysis_fingerprint=app_analysis_fingerprint,
            sp_result_fingerprint=sp_result_fingerprint,
            run_signal_processing_version=run_signal_processing_version,
            session_signal_processing_version=session_signal_processing_version,
            software_commit_sha=software_commit_sha,
        ),
    )


def early_failure_context(**overrides) -> DecisionContext:
    """无 APP run / 无质量结果的合法 Tier-A 上下文。"""

    defaults = {
        "quality_label": None,
        "window_id": None,
        "analysis_allowed": None,
        "app_run_id": None,
        "app_analysis_fingerprint": None,
        "sp_result_fingerprint": None,
        "run_signal_processing_version": None,
        "session_signal_processing_version": SP_VERSION,
    }
    defaults.update(overrides)
    return make_context(**defaults)
