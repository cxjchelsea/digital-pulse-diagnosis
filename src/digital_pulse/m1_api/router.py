"""FastAPI router for the M1-P3C read-only analysis interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from .errors import http_error
from .models import ReplayRequest, ReplayResponse
from .services import M1_API_VERSION, M1AnalysisQueryService


def create_m1_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/m1", tags=["M1-P3C"])
    service = M1AnalysisQueryService(root)

    @router.get("/sessions")
    def list_sessions():
        return service.list_sessions()

    @router.get("/sessions/{session_id}")
    def session_detail(session_id: str):
        return service.session_detail(session_id)

    @router.get("/sessions/{session_id}/channels")
    def channels(session_id: str, run_id: str | None = None, max_points: int | None = None):
        if max_points is not None and max_points <= 0:
            raise http_error(400, "invalid_request", "max_points must be positive.")
        return service.channels(session_id, run_id=run_id, max_points=max_points)

    @router.get("/sessions/{session_id}/analysis")
    def analysis(session_id: str, run_id: str | None = None):
        return service.analysis(session_id, run_id=run_id)

    @router.get("/sessions/{session_id}/runs")
    def runs(session_id: str):
        return service.runs(session_id)

    @router.get("/sessions/{session_id}/runs/{run_id}")
    def run_detail(session_id: str, run_id: str):
        return service.run_detail(session_id, run_id)

    @router.post("/sessions/{session_id}/replay")
    def replay(session_id: str, request: ReplayRequest):
        result = service.replay(
            session_id,
            software_commit_sha=request.software_commit_sha,
            persist=request.persist,
            run_id=request.run_id,
        )
        return ReplayResponse(
            api_version=M1_API_VERSION,
            session_id=result.session_id,
            run_id=result.run_id,
            persisted=result.persisted,
            sp_result_sha256=result.sp_result.result_sha256,
            analysis=result.analysis.to_dict(),
        )

    return router


__all__ = ["M1_API_VERSION", "create_m1_router"]
