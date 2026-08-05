"""FastAPI router for D3 fault-matrix reports and abortable runtime sessions."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from digital_pulse.d3_experiment import D3ReportStore, run_d3_experiment
from digital_pulse.d3_runtime import ConflictError, D3RuntimeRegistry, validate_run_id


class D3ExperimentRequest(BaseModel):
    case_ids: list[str] | None = Field(None, min_length=1, max_length=20)
    seed: int = 20260805


class D3RuntimeCreateRequest(BaseModel):
    targets_au: list[float] | None = Field(None, min_length=1, max_length=8)
    seed: int = 20260805
    acquire_s: float = Field(0.5, gt=0, le=30)
    max_duration_s: float = Field(60.0, gt=0, le=600)
    hold: bool = Field(
        False,
        description="If true, remain in ACQUIRE until ABORT (for abort closed-loop demos).",
    )


def create_d3_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/experiments/d3", tags=["D3"])
    store = D3ReportStore(root)
    registry = D3RuntimeRegistry()

    @router.post("/run")
    def run(request: D3ExperimentRequest):
        try:
            report = run_d3_experiment(
                tuple(request.case_ids) if request.case_ids is not None else None,
                seed=request.seed,
            )
            store.save(report)
            return report
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/runs")
    def create_run(request: D3RuntimeCreateRequest):
        try:
            targets = tuple(request.targets_au) if request.targets_au is not None else None
            session = registry.create(
                targets=targets,
                seed=request.seed,
                acquire_s=request.acquire_s,
                max_duration_s=request.max_duration_s,
                hold=request.hold,
            )
            return session.snapshot()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        try:
            validate_run_id(run_id)
            return registry.get(run_id).snapshot()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "D3 run not found") from exc

    @router.post("/runs/{run_id}/abort")
    def abort_run(run_id: str):
        try:
            validate_run_id(run_id)
            return registry.get(run_id).request_abort()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "D3 run not found") from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/runs/{run_id}/events")
    def run_events(run_id: str):
        try:
            validate_run_id(run_id)
            snap = registry.get(run_id).snapshot()
            return {"run_id": run_id, "events": snap["events"], "timeline": snap["timeline"]}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "D3 run not found") from exc

    @router.get("/{report_sha256}")
    def report(report_sha256: str):
        try:
            return store.load(report_sha256)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "D3 report not found") from exc

    @router.get("/{report_sha256}/events")
    def events(report_sha256: str):
        try:
            saved = store.load(report_sha256)
            return {"report_sha256": report_sha256, "events": saved["events"]}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "D3 report not found") from exc

    @router.post("/{report_sha256}/replay")
    def replay(report_sha256: str):
        try:
            identical, replayed = store.replay(report_sha256)
            return {"identical": identical, "report": replayed}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "D3 report not found") from exc

    return router
