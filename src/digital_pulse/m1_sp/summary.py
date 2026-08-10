"""Deterministic, oracle-free summaries for SP result comparison."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any

import numpy as np

from .models import SPProcessingResult

SP_RESULT_FINGERPRINT_VERSION = "sp-result-fingerprint:v2"

# Explicit top-level inventory: additions to SPProcessingResult must be assigned
# deliberately to one of these sets instead of silently entering or escaping the
# semantic hash.
SP_RESULT_SEMANTIC_FIELDS = frozenset(
    {
        "source_type",
        "processing_status",
        "quality_results",
        "blocking_codes",
        "limitations",
        "processing_version",
        "parameter_version",
        "parameter_status",
        "configuration_digest",
        "engineering_unit_conversion",
        "stage_result",
    }
)
SP_RESULT_EXCLUDED_FIELDS = frozenset(
    {
        "session_id",  # container identity, not algorithm output
        "software_commit_sha",  # execution/code provenance
        "result_sha256",  # derived from this payload
    }
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _array_summary(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(value)
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
    }


def _canonical(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return _array_summary(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def sp_result_fingerprint(result: SPProcessingResult) -> dict[str, Any]:
    """Complete machine-semantic payload with explicit provenance exclusions.

    The normalized input snapshot is not repeated: session/container identity,
    host receive timestamps, and transport-reader provenance are inputs rather
    than SP output. Its semantic consequences are represented by integrity,
    windows, metrics, filters, beats, references, and formal quality results.
    """

    quality_results = [
        {
            "window_id": item.window_id,
            "label": item.label.value,
            "reason_codes": list(item.reason_codes),
            "metrics": _canonical(dict(item.metrics)),
            "score": item.score,
            "confidence": item.confidence,
            "parameter_version": item.parameter_version,
            "parameter_status": item.parameter_status.value,
        }
        for item in result.quality_results
    ]

    stage = result.stage_result
    preprocessing = stage.preprocessing
    return {
        "fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "source_type": result.source_type.value,
        "processing_status": result.processing_status,
        "quality_results": quality_results,
        "blocking_codes": list(result.blocking_codes),
        "limitations": list(result.limitations),
        "processing_version": result.processing_version,
        "parameter_version": result.parameter_version,
        "parameter_status": result.parameter_status.value,
        "configuration_digest": result.configuration_digest,
        "engineering_unit_conversion": _canonical(result.engineering_unit_conversion),
        "stage_result": {
            "processing_status": stage.processing_status,
            "quality_results": _canonical(stage.quality_results),
            "blocking_codes": list(stage.blocking_codes),
            "processing_version": stage.processing_version,
            "parameter_version": stage.parameter_version,
            "parameter_status": stage.parameter_status.value,
            "configuration_digest": stage.configuration_digest,
            "preprocessing": {
                "integrity": _canonical(preprocessing.integrity),
                "windows": _canonical(preprocessing.windows),
                "processing_version": preprocessing.processing_version,
                "parameter_version": preprocessing.parameter_version,
                "parameter_digest": preprocessing.parameter_digest,
            },
            "metrics_by_window": _canonical(stage.metrics_by_window),
            "evaluations_by_window": _canonical(stage.evaluations_by_window),
            "filter_views_by_window": _canonical(stage.filter_views_by_window),
            "beats_by_window": _canonical(stage.beats_by_window),
            "reference_by_window": _canonical(stage.reference_by_window),
        },
    }


def sp_result_sha256(result: SPProcessingResult) -> str:
    """Hash semantic output only; software_commit_sha is intentionally absent."""

    return hashlib.sha256(canonical_json_bytes(sp_result_fingerprint(result))).hexdigest()


def compare_sp_results(left: SPProcessingResult, right: SPProcessingResult) -> bool:
    return (
        left.result_sha256 == right.result_sha256
        and sp_result_fingerprint(left) == sp_result_fingerprint(right)
    )


def summarize_sp_result(result: SPProcessingResult) -> dict[str, Any]:
    """Small golden surface: structural output plus selected formal metrics."""

    quality = []
    for item in result.quality_results:
        quality.append(
            {
                "window_id": item.window_id,
                "label": item.label.value,
                "reason_codes": list(item.reason_codes),
                "formal_metrics": _canonical(dict(item.metrics)),
            }
        )
    references = []
    for window_id, reference in sorted(result.reference_by_window.items()):
        references.append(
            {
                "window_id": window_id,
                "matched_count": int(reference.matched_count),
                "pulse_beat_count": int(reference.pulse_beat_count),
                "ppg_beat_count": int(reference.ppg_beat_count),
                "reference_available": bool(reference.reference_available),
            }
        )
    return {
        "fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "processing_status": result.processing_status,
        "blocking_codes": list(result.blocking_codes),
        "window_ids": [window.window_id for window in result.windows],
        "window_count": len(result.windows),
        "quality_results": quality,
        "references": references,
        "processing_version": result.processing_version,
        "parameter_version": result.parameter_version,
        "configuration_digest": result.configuration_digest,
        "result_sha256": result.result_sha256,
    }
