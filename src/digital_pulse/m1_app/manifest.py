"""Canonical APP manifest serialization and atomic file updates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from digital_pulse.m1_simulator.artifacts import write_text_atomic

from .errors import M1AppError
from .models import AppManifest


def canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise M1AppError("manifest_invalid", "Value cannot be serialized as canonical JSON.", asset="app/manifest.json") from exc
    return (text + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise M1AppError("manifest_invalid", "JSON object contains a duplicate key.", asset="manifest")
        result[key] = value
    return result


def loads_strict_json(text: str, *, asset: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except M1AppError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise M1AppError("manifest_invalid", "JSON content is invalid.", asset=asset) from exc


def load_app_manifest(path: Path) -> AppManifest:
    if not path.is_file():
        raise M1AppError("manifest_invalid", "APP manifest is missing.", asset="app/manifest.json")
    try:
        payload = loads_strict_json(path.read_text(encoding="utf-8"), asset="app/manifest.json")
    except (OSError, UnicodeError) as exc:
        raise M1AppError("manifest_invalid", "APP manifest cannot be read.", asset="app/manifest.json") from exc
    if not isinstance(payload, Mapping):
        raise M1AppError("manifest_invalid", "APP manifest must be a JSON object.", asset="app/manifest.json")
    return AppManifest.from_dict(payload)


def write_app_manifest_atomic(path: Path, manifest: AppManifest) -> None:
    manifest.validate()
    payload = canonical_json_bytes(manifest.to_dict()).decode("utf-8")
    try:
        write_text_atomic(path, payload)
    except OSError as exc:
        raise M1AppError("persistence_failed", "APP manifest update failed.", asset="app/manifest.json") from exc
