"""M1-P3E frozen M1Report projection.

纯投影层：仅消费已冻结的 Session / committed AppAnalysis / run provenance，
不重跑 SP、不发明 P4 决策、不推导正式客观参数。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from digital_pulse.m1_contracts import (
    LimitationCode,
    M1Report,
    M1Session,
    ParameterStatus,
    ReportStatus,
    SourceType,
    from_dict_report,
)

from .errors import M1AppError
from .manifest import canonical_json_bytes
from .models import AppProvenance


# 报告投影版本与 APP/SP 处理版本分离
M1_REPORT_PROJECTION_VERSION = "m1-p3e-report-projection-v1"
REPORT_ASSET_PRODUCER = "m1-p3e-report-projector"
REPORT_ASSET_RELATIVE_PATH = "report.json"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_FROZEN_OBJECTIVE_PARAMETER_KEYS = frozenset(
    {"heart_rate_bpm", "beat_count", "pulse_amplitude_raw", "valid_duration_s"}
)

# 仅识别仓库已冻结的确切 abort 完成原因；禁止子串启发式
RECOGNIZED_ABORT_COMPLETION_REASONS = frozenset({"abort_and_release"})

# AppAnalysis 内部限制码 → 冻结 M1Report LimitationCode
_APP_LIMITATION_TO_REPORT: Mapping[str, LimitationCode] = {
    "synthetic_only": LimitationCode.SYNTHETIC_INPUT,
    LimitationCode.SYNTHETIC_INPUT.value: LimitationCode.SYNTHETIC_INPUT,
    LimitationCode.PENDING_H1_CALIBRATION.value: LimitationCode.PENDING_H1_CALIBRATION,
    LimitationCode.NOT_HARDWARE_VALIDATED.value: LimitationCode.NOT_HARDWARE_VALIDATED,
    LimitationCode.NOT_FOR_MEDICAL_USE.value: LimitationCode.NOT_FOR_MEDICAL_USE,
}

_PARAMETER_STATUS_RANK = {
    ParameterStatus.PENDING_H1_CALIBRATION: 0,
    ParameterStatus.SYNTHETIC_ONLY: 1,
    ParameterStatus.CANDIDATE: 2,
    ParameterStatus.FROZEN: 3,
}


@dataclass(frozen=True, slots=True)
class ReportProjectionInput:
    """报告投影的权威输入集合。"""

    session: M1Session
    analysis: Mapping[str, Any]
    run_id: str
    run_provenance: AppProvenance
    generated_at_utc: str


class M1PreAcceptanceReportBuilder:
    """将已提交分析投影为冻结 M1Report（无 I/O、无时钟）。"""

    def build(self, projection_input: ReportProjectionInput) -> M1Report:
        _validate_projection_input(projection_input)
        session = projection_input.session
        analysis = projection_input.analysis
        gate = analysis["gate"]
        analysis_allowed = bool(gate["analysis_allowed"])
        formal_parameters_allowed = bool(gate["formal_parameters_allowed"])

        report_id = deterministic_report_id(
            session_id=session.session_id,
            run_id=projection_input.run_id,
            analysis_semantic_fingerprint=str(analysis["semantic_fingerprint_sha256"]),
        )
        quality_summary = _map_quality_summary(analysis)
        integrity_summary = _map_integrity_summary(session)
        report_status = _map_report_status(
            session=session,
            analysis=analysis,
            analysis_allowed=analysis_allowed,
        )
        limitations = _map_limitations(session=session, analysis=analysis)
        parameter_status = _map_parameter_status(session=session, analysis=analysis)
        objective_parameters = _map_objective_parameters(
            analysis_allowed=analysis_allowed,
            formal_parameters_allowed=formal_parameters_allowed,
            formal_parameters=analysis.get("formal_parameters"),
        )
        decision_summary = {
            "final_action": None,
            "decision_ids": [],
            "reason_codes": [],
        }
        version_manifest = _map_version_manifest(
            session=session,
            analysis=analysis,
            run_provenance=projection_input.run_provenance,
        )
        failure_summary = _map_failure_summary(
            report_status=report_status,
            session=session,
            analysis=analysis,
        )

        report = M1Report(
            report_id=report_id,
            session_id=session.session_id,
            source_type=session.source_type,
            report_status=report_status,
            analysis_allowed=analysis_allowed,
            quality_summary=quality_summary,
            integrity_summary=integrity_summary,
            objective_parameters=objective_parameters,
            decision_summary=decision_summary,
            version_manifest=version_manifest,
            limitations=limitations,
            generated_at_utc=projection_input.generated_at_utc,
            parameter_status=parameter_status,
            failure_summary=failure_summary,
        )
        report.validate()
        report.validate_schema()
        return report


def deterministic_report_id(
    *,
    session_id: str,
    run_id: str,
    analysis_semantic_fingerprint: str,
) -> str:
    """由投影版本与已提交分析真值确定性派生 report_id。"""

    payload = {
        "projection_version": M1_REPORT_PROJECTION_VERSION,
        "session_id": session_id,
        "run_id": run_id,
        "app_analysis_semantic_fingerprint": analysis_semantic_fingerprint,
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    report_id = f"m1-report-{digest}"
    if len(report_id) > 128:
        raise M1AppError("report_projection_failed", "Deterministic report_id exceeds contract length.", asset="report")
    return report_id


def report_canonical_bytes(report: M1Report) -> bytes:
    """冻结报告的规范序列化字节。"""

    return canonical_json_bytes(report.to_dict())


def parse_and_validate_report(payload: Mapping[str, Any]) -> M1Report:
    """解析并同时执行合约 / JSON Schema 校验。"""

    try:
        report = from_dict_report(payload)
    except Exception as exc:
        raise M1AppError(
            "raw_asset_corrupted",
            "Persisted report failed contract parsing.",
            asset="report",
        ) from exc
    try:
        report.validate_schema()
    except Exception as exc:
        raise M1AppError(
            "raw_asset_corrupted",
            "Persisted report failed schema validation.",
            asset="report",
        ) from exc
    return report


def assert_report_semantic_linkage(
    *,
    persisted: M1Report,
    expected: M1Report,
) -> None:
    """校验持久化报告与同锚点重投影语义完全一致。"""

    if report_canonical_bytes(persisted) != report_canonical_bytes(expected):
        raise M1AppError(
            "report_semantic_linkage_mismatch",
            "Persisted report does not match expected projection from committed analysis.",
            asset="report",
        )


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise M1AppError(
            "report_projection_failed",
            f"Report projection requires mapping field {field_name}.",
            asset="analysis",
        )
    return value


def _validate_projection_input(projection_input: ReportProjectionInput) -> None:
    """投影前 fail-closed 校验；不静默填默认值。"""

    session = projection_input.session
    analysis = projection_input.analysis
    if not isinstance(projection_input.run_id, str) or not projection_input.run_id:
        raise M1AppError("report_projection_failed", "run_id is required for report projection.", asset="run_id")
    if not isinstance(projection_input.generated_at_utc, str) or not projection_input.generated_at_utc:
        raise M1AppError(
            "report_projection_failed",
            "generated_at_utc must be supplied explicitly.",
            asset="generated_at_utc",
        )
    projection_input.run_provenance.validate()

    required_top = {
        "schema_version",
        "session",
        "gate",
        "provenance",
        "semantic_fingerprint_sha256",
        "limitations",
        "integrity_summary",
    }
    missing = sorted(name for name in required_top if name not in analysis)
    if missing:
        raise M1AppError(
            "report_projection_failed",
            "Committed analysis is missing required fields for report projection.",
            asset="analysis",
            details={"missing": missing},
        )

    analysis_session = _require_mapping(analysis["session"], field_name="session")
    gate = _require_mapping(analysis["gate"], field_name="gate")
    provenance = _require_mapping(analysis["provenance"], field_name="provenance")

    analysis_session_id = analysis_session.get("session_id")
    if analysis_session_id != session.session_id:
        raise M1AppError(
            "report_projection_failed",
            "Analysis session_id does not match report session.",
            asset="analysis",
        )

    fingerprint = analysis.get("semantic_fingerprint_sha256")
    if not isinstance(fingerprint, str) or not _HEX_64.fullmatch(fingerprint):
        raise M1AppError(
            "report_projection_failed",
            "Analysis semantic fingerprint is missing or invalid.",
            asset="analysis",
        )

    for required_gate_field in (
        "analysis_allowed",
        "formal_parameters_allowed",
        "blocking_codes",
        "limitations",
        "gate_version",
    ):
        if required_gate_field not in gate:
            raise M1AppError(
                "report_projection_failed",
                "Analysis gate is incomplete.",
                asset="analysis",
            )
    if not isinstance(gate["analysis_allowed"], bool) or not isinstance(gate["formal_parameters_allowed"], bool):
        raise M1AppError(
            "report_projection_failed",
            "Analysis gate boolean fields are invalid.",
            asset="analysis",
        )
    analysis_software_sha = provenance.get("app_software_commit_sha")
    analysis_processing_version = provenance.get("app_processing_version")
    sp_processing_version = provenance.get("sp_processing_version")
    if not isinstance(analysis_software_sha, str) or not isinstance(sp_processing_version, str):
        raise M1AppError(
            "report_projection_failed",
            "Analysis provenance is incomplete.",
            asset="analysis",
        )
    if analysis_software_sha != projection_input.run_provenance.software_commit_sha:
        raise M1AppError(
            "report_projection_failed",
            "Analysis software commit SHA does not match run provenance.",
            asset="analysis",
        )
    if (
        isinstance(analysis_processing_version, str)
        and analysis_processing_version != projection_input.run_provenance.app_processing_version
    ):
        raise M1AppError(
            "report_projection_failed",
            "Analysis processing version does not match run provenance.",
            asset="analysis",
        )


def _map_quality_summary(analysis: Mapping[str, Any]) -> dict[str, Any]:
    quality = analysis.get("quality")
    if quality is None:
        return {"primary_label": None, "reason_codes": [], "window_ids": []}
    if not isinstance(quality, Mapping):
        raise M1AppError("report_projection_failed", "Analysis quality must be an object or null.", asset="analysis")
    window_id = quality.get("window_id")
    window_ids: list[str] = []
    if isinstance(window_id, str) and window_id:
        window_ids = [window_id]
    reason_codes = quality.get("reason_codes") or []
    if not isinstance(reason_codes, list) or any(not isinstance(item, str) or not item for item in reason_codes):
        raise M1AppError("report_projection_failed", "Quality reason_codes are invalid.", asset="analysis")
    primary_label = quality.get("label")
    if primary_label is not None and not isinstance(primary_label, str):
        raise M1AppError("report_projection_failed", "Quality label is invalid.", asset="analysis")
    return {
        "primary_label": primary_label,
        "reason_codes": list(reason_codes),
        "window_ids": window_ids,
    }


def _map_integrity_summary(session: M1Session) -> dict[str, Any]:
    integrity = session.integrity_summary
    return {
        "frame_count": int(integrity.frame_count),
        "crc_error_count": int(integrity.crc_error_count),
        "missing_frame_count": int(integrity.missing_frame_count),
        "timestamp_error_count": int(integrity.timestamp_error_count),
        "raw_persistence_status": integrity.raw_persistence_status.value,
    }


def _map_report_status(
    *,
    session: M1Session,
    analysis: Mapping[str, Any],
    analysis_allowed: bool,
) -> ReportStatus:
    """确定性状态表：禁止子串匹配。"""

    completion_reason = session.completion_reason
    if completion_reason in RECOGNIZED_ABORT_COMPLETION_REASONS:
        return ReportStatus.ABORTED
    if not session.completed:
        return ReportStatus.INCOMPLETE

    quality = analysis.get("quality")
    if isinstance(quality, Mapping) and quality.get("label") == "manual_review_required":
        return ReportStatus.MANUAL_REVIEW_REQUIRED
    gate = analysis["gate"]
    blocking = gate.get("blocking_codes") or []
    if isinstance(blocking, list) and "quality_manual_review_required" in blocking:
        return ReportStatus.MANUAL_REVIEW_REQUIRED

    if not analysis_allowed:
        return ReportStatus.FAILED
    return ReportStatus.COMPLETE


def _map_limitations(*, session: M1Session, analysis: Mapping[str, Any]) -> tuple[LimitationCode, ...]:
    collected: list[LimitationCode] = []
    seen: set[str] = set()

    def add(code: LimitationCode) -> None:
        if code.value not in seen:
            seen.add(code.value)
            collected.append(code)

    # 始终要求非医疗用途
    add(LimitationCode.NOT_FOR_MEDICAL_USE)

    # 会话冻结限制（仅枚举内）
    for item in session.limitations:
        add(item if isinstance(item, LimitationCode) else LimitationCode(item))

    # 分析限制：显式映射，禁止盲拷贝 synthetic_only
    raw_limitations = analysis.get("limitations") or []
    if not isinstance(raw_limitations, list):
        raise M1AppError("report_projection_failed", "Analysis limitations must be a list.", asset="analysis")
    for raw in raw_limitations:
        if not isinstance(raw, str):
            raise M1AppError("report_projection_failed", "Analysis limitation entries must be strings.", asset="analysis")
        mapped = _APP_LIMITATION_TO_REPORT.get(raw)
        if mapped is None:
            # 未知内部码：fail closed，不写入报告
            raise M1AppError(
                "report_projection_failed",
                "Analysis contains unsupported limitation for M1Report mapping.",
                asset="analysis",
                details={"limitation": raw},
            )
        add(mapped)

    if session.source_type is SourceType.SIMULATOR or (
        isinstance(session.source_type, str) and session.source_type == SourceType.SIMULATOR.value
    ):
        add(LimitationCode.SYNTHETIC_INPUT)

    return tuple(collected)


def _map_parameter_status(*, session: M1Session, analysis: Mapping[str, Any]) -> ParameterStatus:
    session_status = session.parameter_status
    quality = analysis.get("quality")
    if not isinstance(quality, Mapping) or quality.get("parameter_status") is None:
        return session_status
    try:
        quality_status = ParameterStatus(str(quality["parameter_status"]))
    except ValueError as exc:
        raise M1AppError(
            "report_projection_failed",
            "Quality parameter_status is not a frozen enum value.",
            asset="analysis",
        ) from exc
    # 保守规则：取更保守（秩更低）的状态；禁止静默升格
    if _PARAMETER_STATUS_RANK[quality_status] < _PARAMETER_STATUS_RANK[session_status]:
        return quality_status
    return session_status


def _map_objective_parameters(
    *,
    analysis_allowed: bool,
    formal_parameters_allowed: bool,
    formal_parameters: Any,
) -> dict[str, Any] | None:
    # pre-H1：formal_parameters_allowed 恒为 false → 必须为 null
    if not analysis_allowed or not formal_parameters_allowed:
        return None
    if formal_parameters is None:
        return None
    if not isinstance(formal_parameters, Mapping):
        raise M1AppError(
            "report_projection_failed",
            "formal_parameters must be an object or null.",
            asset="analysis",
        )
    # 仅转发冻结 schema 键；未知键 fail-closed，禁止派生计算
    unknown_keys = sorted(str(key) for key in formal_parameters if key not in _FROZEN_OBJECTIVE_PARAMETER_KEYS)
    if unknown_keys:
        raise M1AppError(
            "report_projection_failed",
            "formal_parameters contains keys outside the frozen M1Report schema.",
            asset="analysis",
            details={"unknown_keys": unknown_keys},
        )
    mapped = {
        key: formal_parameters[key]
        for key in ("heart_rate_bpm", "beat_count", "pulse_amplitude_raw", "valid_duration_s")
        if key in formal_parameters
    }
    return mapped or None


def _map_version_manifest(
    *,
    session: M1Session,
    analysis: Mapping[str, Any],
    run_provenance: AppProvenance,
) -> dict[str, Any]:
    provenance = analysis["provenance"]
    signal_processing_version = provenance.get("sp_processing_version")
    if signal_processing_version is None:
        signal_processing_version = session.versions.signal_processing_version

    configuration_digest = run_provenance.configuration_digest
    if configuration_digest is None:
        configuration_digest = session.versions.configuration_digest

    return {
        "protocol_version": session.protocol_version,
        "calibration_version": session.versions.calibration_version,
        "signal_processing_version": signal_processing_version,
        "decision_rule_version": None,
        "software_commit_sha": run_provenance.software_commit_sha,
        "configuration_digest": configuration_digest,
    }


def _map_failure_summary(
    *,
    report_status: ReportStatus,
    session: M1Session,
    analysis: Mapping[str, Any],
) -> str | None:
    if report_status is ReportStatus.COMPLETE:
        return None
    parts: list[str] = []
    if session.completion_reason:
        parts.append(f"completion:{session.completion_reason}")
    gate = analysis["gate"]
    blocking = gate.get("blocking_codes") or []
    if isinstance(blocking, list):
        for code in blocking:
            if isinstance(code, str) and code:
                parts.append(f"gate:{code}")
    quality = analysis.get("quality")
    if isinstance(quality, Mapping):
        for code in quality.get("reason_codes") or []:
            if isinstance(code, str) and code:
                parts.append(f"quality:{code}")
    if not parts:
        parts.append(f"report_status:{report_status.value}")
    summary = "; ".join(parts)
    return summary[:1024]


__all__ = [
    "M1_REPORT_PROJECTION_VERSION",
    "M1PreAcceptanceReportBuilder",
    "REPORT_ASSET_PRODUCER",
    "REPORT_ASSET_RELATIVE_PATH",
    "RECOGNIZED_ABORT_COMPLETION_REASONS",
    "ReportProjectionInput",
    "assert_report_semantic_linkage",
    "deterministic_report_id",
    "parse_and_validate_report",
    "report_canonical_bytes",
]
