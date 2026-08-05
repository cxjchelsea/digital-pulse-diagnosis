"""FastAPI router for persisted D3 fault-matrix experiments."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from digital_pulse.d3_experiment import D3ReportStore, run_d3_experiment


class D3ExperimentRequest(BaseModel):
    case_ids: list[str] | None = Field(None, min_length=1, max_length=20)
    seed: int = 20260805


def create_d3_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/experiments/d3", tags=["D3"])
    store = D3ReportStore(root)

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
