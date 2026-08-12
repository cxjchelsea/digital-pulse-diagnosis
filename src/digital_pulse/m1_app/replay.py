"""Replay persisted raw sessions through frozen SP and APP projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from digital_pulse.m1_simulator.artifacts import ArtifactError
from digital_pulse.m1_simulator.replay import ReplayDataSource
from digital_pulse.m1_sp import SPProcessingProvenance, SPProcessor
from digital_pulse.m1_sp.models import SPProcessingResult

from .analysis import AppAnalysis, AnalysisProjector, create_replay_app_provenance
from .errors import M1AppError
from .loader import AppSessionLoader, LoadedAppSession
from .manifest import canonical_json_bytes
from .models import APP_PROCESSING_VERSION_P3B, AppAssetRole, AppExecutionMode, AppProvenance
from .persistence import AppAssetWrite, AppPersistence
from .sp_serialization import sp_result_assets


@dataclass(frozen=True, slots=True)
class ReplaySessionSource:
    loaded: LoadedAppSession
    data_source: ReplayDataSource

    @property
    def session(self):
        return self.data_source.session

    def samples(self):
        return self.data_source.samples()


@dataclass(frozen=True, slots=True)
class ReplayAnalysisResult:
    session_id: str
    run_id: str | None
    persisted: bool
    sp_result: SPProcessingResult
    analysis: AppAnalysis


class ReplayAnalysisService:
    """Read persisted raw, rerun SP, project APP analysis, optionally persist."""

    def __init__(
        self,
        sessions_root: Path,
        *,
        loader: AppSessionLoader | None = None,
        persistence: AppPersistence | None = None,
        processor: SPProcessor | None = None,
        projector: AnalysisProjector | None = None,
    ):
        self._sessions_root = Path(sessions_root)
        self._loader = loader or AppSessionLoader(sessions_root)
        self._persistence = persistence or AppPersistence(sessions_root)
        self._processor = processor or SPProcessor()
        self._projector = projector or AnalysisProjector()

    def source_for(self, session_id: str) -> ReplaySessionSource:
        loaded = self._loader.load(session_id, verify_runs=False)
        try:
            source = ReplayDataSource(
                loaded.session_root,
                allow_incomplete=not loaded.session.completed,
            )
        except ArtifactError as exc:
            raise M1AppError("replay_failed", "Persisted raw session cannot be replayed.", asset=session_id) from exc
        return ReplaySessionSource(loaded=loaded, data_source=source)

    def replay(
        self,
        session_id: str,
        *,
        software_commit_sha: str,
        persist: bool = False,
        run_id: str | None = None,
    ) -> ReplayAnalysisResult:
        if persist and not run_id:
            raise M1AppError("manifest_invalid", "Persisted replay requires an explicit run_id.", asset="run_id")
        if not persist and run_id is not None:
            raise M1AppError("manifest_invalid", "Read-only replay must not receive a run_id.", asset="run_id")

        source = self.source_for(session_id)
        provenance = SPProcessingProvenance(software_commit_sha=software_commit_sha)
        try:
            sp_result = self._processor.process(source.session, source.samples(), provenance=provenance)
        except Exception as exc:
            raise M1AppError("sp_processing_failed", "SP processing failed during APP replay.", asset=session_id) from exc
        app_provenance = create_replay_app_provenance(software_commit_sha)
        analysis = self._projector.project(
            session=source.session,
            sp_result=sp_result,
            app_provenance=app_provenance,
        )

        if persist:
            assert run_id is not None
            self._persistence.commit_run(
                session_id,
                run_id,
                provenance=app_provenance,
                assets=_replay_assets(sp_result, analysis),
                allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
            )
        return ReplayAnalysisResult(
            session_id=session_id,
            run_id=run_id if persist else None,
            persisted=persist,
            sp_result=sp_result,
            analysis=analysis,
        )


def _replay_assets(sp_result: SPProcessingResult, analysis: AppAnalysis) -> tuple[AppAssetWrite, ...]:
    return (
        *sp_result_assets(sp_result),
        AppAssetWrite(
            role=AppAssetRole.ANALYSIS,
            relative_path="analysis.json",
            content=canonical_json_bytes(analysis.to_dict()),
            media_type="application/json",
            producer="m1-p3b-analysis-projector",
            version=APP_PROCESSING_VERSION_P3B,
        ),
    )
