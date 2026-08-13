"""Stable, sanitized HTTP error mapping for M1-P3C."""

from __future__ import annotations

from fastapi import HTTPException

from digital_pulse.m1_app import M1AppError


_APP_ERROR_MAP: dict[str, tuple[int, str]] = {
    "path_escape": (400, "invalid_session_id"),
    "symlink_escape": (400, "invalid_session_id"),
    "session_not_found": (404, "session_not_found"),
    "raw_asset_missing": (404, "analysis_not_available"),
    "manifest_invalid": (422, "invalid_manifest"),
    "raw_asset_corrupted": (422, "artifact_corrupted"),
    "asset_unreadable": (422, "artifact_corrupted"),
    "artifact_conflict": (409, "artifact_conflict"),
    "replay_failed": (422, "replay_failed"),
    "sp_processing_failed": (500, "sp_processing_failed"),
    "persistence_failed": (500, "replay_failed"),
}


def error_envelope(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_envelope(code, message))


def app_error_to_http(exc: M1AppError) -> HTTPException:
    status, code = _APP_ERROR_MAP.get(exc.code, (500, "internal_error"))
    if exc.code == "raw_asset_missing" and exc.asset == "run":
        status, code = 404, "run_not_found"
    if exc.code == "raw_asset_corrupted" and exc.asset == "analysis":
        code = "semantic_linkage_mismatch"
    if exc.code == "manifest_invalid" and exc.asset == "run_id":
        code = "invalid_run_id"
        status = 400
    messages = {
        "invalid_session_id": "Session identifier is invalid.",
        "invalid_run_id": "Run identifier is invalid.",
        "session_not_found": "Session not found.",
        "run_not_found": "Run not found.",
        "analysis_not_available": "Analysis is not available.",
        "invalid_manifest": "Session manifest is invalid.",
        "artifact_corrupted": "Registered artifact is corrupted.",
        "semantic_linkage_mismatch": "Registered analysis does not match its SP result.",
        "artifact_conflict": "Artifact conflict.",
        "replay_failed": "Replay failed.",
        "sp_processing_failed": "SP processing failed.",
        "internal_error": "Internal server error.",
    }
    return http_error(status, code, messages[code])
