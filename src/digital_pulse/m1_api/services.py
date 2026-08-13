"""Thin query/replay adapter over P3A/P3B APP services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from digital_pulse.m1_app import AppAssetRole, AppSessionLoader, M1AppError, ReplayAnalysisService
from digital_pulse.m1_app.checksums import verify_asset_ref
from digital_pulse.m1_app.manifest import loads_strict_json
from digital_pulse.m1_app.paths import SafeSessionPath, resolve_session_root
from digital_pulse.m1_simulator.artifacts import ArtifactError
from digital_pulse.m1_simulator.paths import validate_artifact_identifier
from digital_pulse.m1_simulator.replay import ReplayDataSource

from .models import (
    AnalysisResponse,
    ChannelSeries,
    ChannelsResponse,
    RunDetail,
    RunSummary,
    RunsResponse,
    SeriesMetadata,
    SessionDetail,
    SessionSummary,
    SessionsResponse,
)


M1_API_VERSION = "m1-p3c-api-v1"


class M1AnalysisQueryService:
    """Read persisted M1 APP state without creating or recomputing it."""

    def __init__(self, sessions_root: Path):
        self._sessions_root = Path(sessions_root)
        self._loader = AppSessionLoader(self._sessions_root)
        self._replay = ReplayAnalysisService(self._sessions_root, loader=self._loader)

    def list_sessions(self) -> SessionsResponse:
        summaries = []
        if self._sessions_root.is_dir():
            for child in sorted(self._sessions_root.iterdir(), key=lambda item: item.name):
                if child.is_dir() and (child / "manifest.json").is_file():
                    summaries.append(self._session_summary(child.name))
        return SessionsResponse(api_version=M1_API_VERSION, sessions=summaries)

    def session_detail(self, session_id: str) -> SessionDetail:
        loaded = self._loader.load(session_id, verify_runs=False)
        summary = self._summary_from_loaded(loaded)
        manifest = loaded.app_manifest
        return SessionDetail(
            **summary.model_dump(),
            sample_rate_hz=loaded.session.sample_rate_hz,
            configured_channels=list(loaded.session.configured_channels),
            started_at_utc=loaded.session.started_at_utc,
            ended_at_utc=loaded.session.ended_at_utc,
            parameter_status=loaded.session.parameter_status.value,
            formal_parameters=None,
            formal_parameters_allowed=False,
            limitations=[item.value for item in loaded.session.limitations],
            raw_integrity_assurance=manifest.raw_integrity_assurance.value,
            source_assets=[_safe_asset_ref(item) for item in sorted(manifest.source_assets, key=lambda asset: asset.role.value)],
            runs=[self._run_summary_dict(item) for item in sorted(manifest.runs, key=lambda run: run.run_id)],
        )

    def channels(self, session_id: str, *, run_id: str | None = None, max_points: int | None = None) -> ChannelsResponse:
        loaded = self._loader.load(session_id, verify_runs=True)
        samples = list(ReplayDataSource(loaded.session_root, allow_incomplete=not loaded.session.completed).samples())
        raw = {
            "timestamps": _series("timestamps", [item.device_time_us for item in samples], max_points),
            "pulse": _series("pulse", [item.pulse.value for item in samples], max_points),
            "load": _series("load", [item.load.value for item in samples], max_points),
            "ppg": _series("ppg", [item.ppg.value for item in samples], max_points),
        }
        processed: dict[str, ChannelSeries] = {}
        if run_id is not None:
            _validate_run_id(run_id)
            run = self._find_run(loaded.app_manifest.runs, run_id)
            safe_paths = SafeSessionPath(loaded.session_root)
            for asset in sorted(run.assets, key=lambda item: item.relative_path):
                if asset.role is AppAssetRole.SP_SERIES:
                    path = verify_asset_ref(safe_paths, asset)
                    processed[asset.relative_path] = _series(asset.relative_path, np.load(path, allow_pickle=False).tolist(), max_points)
        return ChannelsResponse(api_version=M1_API_VERSION, session_id=session_id, run_id=run_id, raw=raw, processed=processed)

    def runs(self, session_id: str) -> RunsResponse:
        loaded = self._loader.load(session_id, verify_runs=True)
        return RunsResponse(
            api_version=M1_API_VERSION,
            session_id=session_id,
            current_run_id=loaded.app_manifest.current_run_id,
            runs=[self._run_summary(item) for item in sorted(loaded.app_manifest.runs, key=lambda run: run.run_id)],
        )

    def run_detail(self, session_id: str, run_id: str) -> RunDetail:
        loaded = self._loader.load(session_id, verify_runs=True)
        _validate_run_id(run_id)
        run = self._find_run(loaded.app_manifest.runs, run_id)
        return RunDetail(
            api_version=M1_API_VERSION,
            session_id=session_id,
            run_id=run_id,
            run=self._run_summary_dict(run),
            assets=[_safe_asset_ref(item) for item in sorted(run.assets, key=lambda asset: (asset.role.value, asset.relative_path))],
        )

    def analysis(self, session_id: str, *, run_id: str | None = None) -> AnalysisResponse:
        loaded = self._loader.load(session_id, verify_runs=True)
        selected_run_id = run_id or loaded.app_manifest.current_run_id
        if selected_run_id is None:
            raise M1AppError("raw_asset_missing", "No committed analysis is available.", asset="analysis")
        _validate_run_id(selected_run_id)
        run = self._find_run(loaded.app_manifest.runs, selected_run_id)
        asset = next((item for item in run.assets if item.role is AppAssetRole.ANALYSIS), None)
        if asset is None:
            raise M1AppError("raw_asset_missing", "Committed run has no analysis asset.", asset="analysis")
        path = verify_asset_ref(SafeSessionPath(loaded.session_root), asset)
        payload = loads_strict_json(path.read_text(encoding="utf-8"), asset=AppAssetRole.ANALYSIS.value)
        if not isinstance(payload, dict):
            raise M1AppError("raw_asset_corrupted", "Analysis asset must be a JSON object.", asset="analysis")
        return AnalysisResponse(api_version=M1_API_VERSION, session_id=session_id, run_id=selected_run_id, analysis=payload)

    def replay(self, session_id: str, *, software_commit_sha: str, persist: bool, run_id: str | None):
        if run_id is not None:
            _validate_run_id(run_id)
        result = self._replay.replay(session_id, software_commit_sha=software_commit_sha, persist=persist, run_id=run_id)
        return result

    def _session_summary(self, session_id: str) -> SessionSummary:
        try:
            loaded = self._loader.load(session_id, verify_runs=False)
            return self._summary_from_loaded(loaded)
        except M1AppError as exc:
            root = resolve_session_root(self._sessions_root, session_id)
            manifest_path = root / "manifest.json"
            if exc.code in {"session_not_found", "path_escape", "symlink_escape"} or not manifest_path.is_file():
                raise
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            return SessionSummary(
                api_version=M1_API_VERSION,
                session_id=str(payload.get("session_id", session_id)),
                source_type=str(payload.get("source_type", "unknown")),
                completed=bool(payload.get("completed", False)),
                completion_reason=payload.get("completion_reason"),
                raw_persistence_status=str(payload.get("integrity_summary", {}).get("raw_persistence_status", "unknown")),
                app_registered=False,
                committed_run_count=0,
                current_run_id=None,
            )

    def _summary_from_loaded(self, loaded) -> SessionSummary:
        return SessionSummary(
            api_version=M1_API_VERSION,
            session_id=loaded.session.session_id,
            source_type=loaded.session.source_type.value,
            completed=loaded.session.completed,
            completion_reason=loaded.session.completion_reason,
            raw_persistence_status=loaded.session.integrity_summary.raw_persistence_status.value,
            app_registered=True,
            committed_run_count=len(loaded.app_manifest.runs),
            current_run_id=loaded.app_manifest.current_run_id,
        )

    @staticmethod
    def _find_run(runs, run_id: str):
        for run in runs:
            if run.run_id == run_id:
                return run
        raise M1AppError("raw_asset_missing", "Run not found.", asset="run")

    @staticmethod
    def _run_summary(run) -> RunSummary:
        return RunSummary(
            run_id=run.run_id,
            state=run.state.value,
            committed_at_utc=run.committed_at_utc,
            execution_mode=run.provenance.execution_mode.value,
            asset_roles=sorted({item.role.value for item in run.assets}),
        )

    def _run_summary_dict(self, run) -> dict[str, Any]:
        return self._run_summary(run).model_dump()


def _safe_asset_ref(asset) -> dict[str, Any]:
    return {
        "role": asset.role.value,
        "relative_path": asset.relative_path,
        "sha256": asset.sha256,
        "size_bytes": asset.size_bytes,
        "media_type": asset.media_type,
        "producer": asset.producer,
        "version": asset.version,
        "checksum_source": asset.checksum_provenance.source.value,
    }


def _validate_run_id(run_id: str) -> str:
    try:
        return validate_artifact_identifier(run_id, name="run_id")
    except ArtifactError as exc:
        raise M1AppError("manifest_invalid", "Run identifier is not filesystem-safe.", asset="run_id") from exc


def _series(name: str, values: list[Any], max_points: int | None) -> ChannelSeries:
    original = len(values)
    returned = values
    downsampled = False
    if max_points is not None and max_points > 0 and original > max_points:
        if max_points == 1:
            returned = values[:1]
        else:
            step = (original - 1) / (max_points - 1)
            indexes = sorted({round(index * step) for index in range(max_points)})
            returned = [values[index] for index in indexes]
        downsampled = True
    return ChannelSeries(
        name=name,
        values=returned,
        metadata=SeriesMetadata(original_count=original, returned_count=len(returned), downsampled=downsampled),
    )
