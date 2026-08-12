"""Deterministic SP result serialization for APP replay runs."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

import numpy as np

from digital_pulse.m1_sp import SP_RESULT_FINGERPRINT_VERSION, sp_result_fingerprint
from digital_pulse.m1_sp.models import SPProcessingResult
from digital_pulse.m1_sp.summary import canonical_json_bytes, summarize_sp_result

from .models import APP_PROCESSING_VERSION_P3B, AppAssetRole
from .persistence import AppAssetWrite


SP_RESULT_SCHEMA_VERSION = "m1-p3b-sp-result-v1"


def sp_result_document(result: SPProcessingResult) -> dict[str, object]:
    return {
        "schema_version": SP_RESULT_SCHEMA_VERSION,
        "fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "summary": summarize_sp_result(result),
        "semantic_fingerprint": sp_result_fingerprint(result),
        "result_sha256": result.result_sha256,
        "processing_version": result.processing_version,
        "parameter_version": result.parameter_version,
        "parameter_digest": result.parameter_digest,
        "software_commit_sha": result.software_commit_sha,
    }


def sp_result_assets(result: SPProcessingResult) -> tuple[AppAssetWrite, ...]:
    assets: list[AppAssetWrite] = [
        AppAssetWrite(
            role=AppAssetRole.SP_RESULT,
            relative_path="sp/result.json",
            content=canonical_json_bytes(sp_result_document(result)),
            media_type="application/json",
            producer="m1-p3b-sp-serialization",
            version=APP_PROCESSING_VERSION_P3B,
        )
    ]
    for relative_path, array in _series_arrays(result):
        assets.append(
            AppAssetWrite(
                role=AppAssetRole.SP_SERIES,
                relative_path=f"sp/series/{relative_path}",
                content=_npy_bytes(array),
                media_type="application/x-npy",
                producer="m1-p3b-sp-serialization",
                version=APP_PROCESSING_VERSION_P3B,
            )
        )
    return tuple(assets)


def _series_arrays(result: SPProcessingResult) -> Iterable[tuple[str, np.ndarray]]:
    for window_id, views in sorted(result.filter_views_by_window.items()):
        for name, series in sorted(views.items()):
            yield f"{window_id}-{name}-values.npy", np.asarray(series.values)
            yield f"{window_id}-{name}-valid-mask.npy", np.asarray(series.valid_mask)


def _npy_bytes(array: np.ndarray) -> bytes:
    with TemporaryDirectory(prefix="m1-p3b-npy-") as temporary:
        path = Path(temporary) / "array.npy"
        np.save(path, np.ascontiguousarray(array), allow_pickle=False)
        return path.read_bytes()
