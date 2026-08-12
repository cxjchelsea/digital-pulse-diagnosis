"""Deterministic APP projection of frozen SP truth for M1-P3B."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from digital_pulse.m1_contracts import M1Session
from digital_pulse.m1_sp.models import QualityMetricsInternal, SPProcessingResult
from digital_pulse.m1_sp.summary import SP_RESULT_FINGERPRINT_VERSION, canonical_json_bytes

from .gating import AnalysisQualityGate, AppGateDecision
from .models import APP_PROCESSING_VERSION_P3B, AppProvenance


APP_ANALYSIS_SCHEMA_VERSION = "m1-p3b-app-analysis-v1"
APP_ANALYSIS_FINGERPRINT_VERSION = "app-analysis-fingerprint:v1"


@dataclass(frozen=True, slots=True)
class AppAnalysis:
    schema_version: str
    session: Mapping[str, Any]
    processing_status: str
    quality: Mapping[str, Any] | None
    gate: AppGateDecision
    formal_parameters: Mapping[str, Any] | None
    limitations: tuple[str, ...]
    integrity_summary: Mapping[str, Any]
    stable_windows: tuple[Mapping[str, Any], ...]
    raw_quality_metrics: Mapping[str, Mapping[str, Any]]
    filter_view_summary: Mapping[str, Mapping[str, Any]]
    beat_summary: Mapping[str, Mapping[str, Any]]
    reference_summary: Mapping[str, Mapping[str, Any]]
    engineering_unit_conversion: Mapping[str, Any]
    provenance: Mapping[str, Any]
    semantic_fingerprint_version: str
    semantic_fingerprint_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session": dict(self.session),
            "processing_status": self.processing_status,
            "quality": None if self.quality is None else dict(self.quality),
            "gate": self.gate.to_dict(),
            "formal_parameters": None if self.formal_parameters is None else dict(self.formal_parameters),
            "limitations": list(self.limitations),
            "integrity_summary": dict(self.integrity_summary),
            "stable_windows": [dict(item) for item in self.stable_windows],
            "raw_quality_metrics": {key: dict(value) for key, value in self.raw_quality_metrics.items()},
            "filter_view_summary": {key: dict(value) for key, value in self.filter_view_summary.items()},
            "beat_summary": {key: dict(value) for key, value in self.beat_summary.items()},
            "reference_summary": {key: dict(value) for key, value in self.reference_summary.items()},
            "engineering_unit_conversion": dict(self.engineering_unit_conversion),
            "provenance": dict(self.provenance),
            "semantic_fingerprint_version": self.semantic_fingerprint_version,
            "semantic_fingerprint_sha256": self.semantic_fingerprint_sha256,
        }


class AnalysisProjector:
    """Project SP output to APP semantic analysis without rerunning algorithms."""

    def __init__(self, *, gate: AnalysisQualityGate | None = None):
        self._gate = gate or AnalysisQualityGate()

    def project(
        self,
        *,
        session: M1Session,
        sp_result: SPProcessingResult,
        app_provenance: AppProvenance,
    ) -> AppAnalysis:
        decision = self._gate.decide(session, sp_result)
        quality = _quality_projection(sp_result)
        payload = {
            "schema_version": APP_ANALYSIS_SCHEMA_VERSION,
            "session": {
                "session_id": session.session_id,
                "source_type": session.source_type.value,
                "completed": session.completed,
                "completion_reason": session.completion_reason,
                "raw_persistence_status": session.integrity_summary.raw_persistence_status.value,
                "parameter_status": session.parameter_status.value,
            },
            "processing_status": sp_result.processing_status,
            "quality": quality,
            "gate": decision.to_dict(),
            "formal_parameters": None,
            "limitations": list(decision.limitations),
            "integrity_summary": _integrity_projection(sp_result),
            "stable_windows": [_window_projection(item) for item in sp_result.windows],
            "raw_quality_metrics": {
                key: _metrics_projection(value)
                for key, value in sorted(sp_result.metrics_by_window.items())
            },
            "filter_view_summary": _filter_projection(sp_result),
            "beat_summary": _beat_projection(sp_result),
            "reference_summary": _reference_projection(sp_result),
            "engineering_unit_conversion": _engineering_projection(sp_result),
            "provenance": {
                "app_processing_version": app_provenance.app_processing_version,
                "app_manifest_schema_version": app_provenance.app_manifest_schema_version,
                "app_execution_mode": app_provenance.execution_mode.value,
                "app_software_commit_sha": app_provenance.software_commit_sha,
                "sp_processing_version": sp_result.processing_version,
                "sp_parameter_version": sp_result.parameter_version,
                "sp_parameter_digest": sp_result.parameter_digest,
                "sp_semantic_fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
                "sp_result_sha256": sp_result.result_sha256,
            },
            "semantic_fingerprint_version": APP_ANALYSIS_FINGERPRINT_VERSION,
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        payload["semantic_fingerprint_sha256"] = digest
        return AppAnalysis(
            schema_version=payload["schema_version"],
            session=payload["session"],
            processing_status=payload["processing_status"],
            quality=payload["quality"],
            gate=decision,
            formal_parameters=payload["formal_parameters"],
            limitations=tuple(payload["limitations"]),
            integrity_summary=payload["integrity_summary"],
            stable_windows=tuple(payload["stable_windows"]),
            raw_quality_metrics=payload["raw_quality_metrics"],
            filter_view_summary=payload["filter_view_summary"],
            beat_summary=payload["beat_summary"],
            reference_summary=payload["reference_summary"],
            engineering_unit_conversion=payload["engineering_unit_conversion"],
            provenance=payload["provenance"],
            semantic_fingerprint_version=payload["semantic_fingerprint_version"],
            semantic_fingerprint_sha256=digest,
        )


def create_replay_app_provenance(software_commit_sha: str) -> AppProvenance:
    from .models import APP_MANIFEST_SCHEMA_VERSION, AppExecutionMode

    return AppProvenance(
        software_commit_sha=software_commit_sha,
        app_processing_version=APP_PROCESSING_VERSION_P3B,
        app_manifest_schema_version=APP_MANIFEST_SCHEMA_VERSION,
        producer="m1-p3b-replay-analysis",
        execution_mode=AppExecutionMode.REPLAY,
        configuration_digest=None,
    )


def compare_app_analysis(left: AppAnalysis, right: AppAnalysis) -> bool:
    return left.to_dict() == right.to_dict()


def _quality_projection(result: SPProcessingResult) -> dict[str, Any] | None:
    if not result.quality_results:
        return None
    item = result.quality_results[0]
    return {
        "window_id": item.window_id,
        "label": item.label.value,
        "reason_codes": list(item.reason_codes),
        "score": item.score,
        "confidence": item.confidence,
        "valid_duration_s": item.valid_duration_s,
        "metrics": dict(item.metrics),
        "parameter_status": item.parameter_status.value,
    }


def _integrity_projection(result: SPProcessingResult) -> dict[str, Any]:
    item = result.integrity
    return {
        "sample_count": item.sample_count,
        "crc_error_count": item.crc_error_count,
        "sequence_error_count": item.sequence_error_count,
        "missing_frame_count": item.missing_frame_count,
        "timestamp_error_count": item.timestamp_error_count,
        "sensor_disconnection_count": item.sensor_disconnection_count,
        "raw_persistence_status": item.raw_persistence_status.value,
        "integrity_ok": item.integrity_ok,
        "pre_quality_blocked": item.pre_quality_blocked,
        "blocking_codes": list(item.blocking_codes),
        "consistency": item.consistency.value,
    }


def _window_projection(window) -> dict[str, Any]:
    return {
        "window_id": window.window_id,
        "start_device_time_us": window.start_device_time_us,
        "end_device_time_us": window.end_device_time_us,
        "sample_count": window.sample_count,
        "duration_s": window.duration_s,
    }


def _metrics_projection(metrics: QualityMetricsInternal) -> dict[str, Any]:
    return {
        "valid_fraction": metrics.valid_fraction,
        "clipping_fraction": metrics.clipping_fraction,
        "baseline_drift_raw": metrics.baseline_drift_raw,
        "pulse_std_raw": metrics.pulse_std_raw,
        "lower_clipping_fraction": metrics.lower_clipping_fraction,
        "upper_clipping_fraction": metrics.upper_clipping_fraction,
        "load_median_raw": metrics.load_median_raw,
        "load_std_raw": metrics.load_std_raw,
        "load_range_raw": metrics.load_range_raw,
        "load_slope_raw_per_s": metrics.load_slope_raw_per_s,
        "motion_metric": metrics.motion_metric,
        "near_constant_metric": metrics.near_constant_metric,
        "valid_sample_count": metrics.valid_sample_count,
        "total_sample_count": metrics.total_sample_count,
        "beat_count": metrics.beat_count,
        "ppg_match_rate": metrics.ppg_match_rate,
    }


def _filter_projection(result: SPProcessingResult) -> dict[str, Mapping[str, Any]]:
    summary: dict[str, Mapping[str, Any]] = {}
    for window_id, views in sorted(result.filter_views_by_window.items()):
        summary[window_id] = {
            key: {
                "mode": value.mode,
                "sample_count": int(value.values.shape[0]),
                "valid_count": int(value.valid_mask.sum()),
                "group_delay_samples": value.group_delay_samples,
                "filter_version": value.filter_version,
                "num_taps": value.num_taps,
            }
            for key, value in sorted(views.items())
        }
    return summary


def _beat_projection(result: SPProcessingResult) -> dict[str, Mapping[str, Any]]:
    return {
        window_id: {
            "beat_count": item.beat_count,
            "interval_mean_ms": item.interval_mean_ms,
            "interval_std_ms": item.interval_std_ms,
            "interval_cv": item.interval_cv,
            "detection_source": item.detection_source,
        }
        for window_id, item in sorted(result.beats_by_window.items())
    }


def _reference_projection(result: SPProcessingResult) -> dict[str, Mapping[str, Any]]:
    return {
        window_id: {
            "pulse_beat_count": item.pulse_beat_count,
            "ppg_beat_count": item.ppg_beat_count,
            "matched_count": item.matched_count,
            "match_rate": item.match_rate,
            "median_lag_ms": item.median_lag_ms,
            "lag_mad_ms": item.lag_mad_ms,
            "reference_available": item.reference_available,
        }
        for window_id, item in sorted(result.reference_by_window.items())
    }


def _engineering_projection(result: SPProcessingResult) -> dict[str, Any]:
    item = result.engineering_unit_conversion
    return {
        "converter_name": item.converter_name,
        "converter_version": item.converter_version,
        "parameter_status": item.parameter_status.value,
        "raw_identity": item.raw_identity,
        "engineering_units_applied": item.engineering_units_applied,
        "conversion_status": item.conversion_status.value,
        "simulation_only": item.simulation_only,
        "real_calibration_pending": item.real_calibration_pending,
    }
