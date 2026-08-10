"""Deterministic, oracle-free summaries for SP result comparison."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any

import numpy as np

from .models import SPProcessingResult


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
    """Return all algorithm-relevant output while excluding execution provenance."""

    return {
        "processing_status": result.processing_status,
        "quality_results": [_canonical(item.to_dict()) for item in result.quality_results],
        "windows": _canonical(result.windows),
        "integrity": _canonical(result.integrity),
        "metrics_by_window": _canonical(result.metrics_by_window),
        "evaluations_by_window": _canonical(result.evaluations_by_window),
        "filters_by_window": _canonical(result.filter_views_by_window),
        "beats_by_window": _canonical(result.beats_by_window),
        "reference_by_window": _canonical(result.reference_by_window),
        "blocking_codes": list(result.blocking_codes),
        "processing_version": result.processing_version,
        "parameter_version": result.parameter_version,
        "parameter_status": result.parameter_status.value,
        "parameter_digest": result.parameter_digest,
    }


def compare_sp_results(left: SPProcessingResult, right: SPProcessingResult) -> bool:
    return sp_result_fingerprint(left) == sp_result_fingerprint(right)


def summarize_sp_result(result: SPProcessingResult) -> dict[str, Any]:
    """Small golden surface: structural output plus selected formal metrics."""

    quality = []
    for item in result.quality_results:
        quality.append(
            {
                "window_id": item.window_id,
                "label": item.label.value,
                "reason_codes": list(item.reason_codes),
                "beat_count": item.metrics.get("beat_count"),
                "ppg_match_rate": item.metrics.get("ppg_match_rate"),
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
        "processing_status": result.processing_status,
        "blocking_codes": list(result.blocking_codes),
        "window_ids": [window.window_id for window in result.windows],
        "quality_results": quality,
        "references": references,
    }
