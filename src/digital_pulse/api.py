"""FastAPI application for simulated P0 acquisition and analysis."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .device import DeviceSimulator, PressureStep, SimulationConfig
from .calibration import CalibrationModel, CalibrationRecord
from .d2_experiment import D2FaultConfig, D2PressureStep, PressureProfile, run_d2_experiment
from .d3_api import create_d3_router
from .firmware import DeviceClient, FirmwareSimulator
from .m1_api import create_m1_router
from .m1_api.errors import app_error_to_http, error_envelope
from .m1_app import M1AppError
from .pipeline import process_session
from .protocol import CommandCode, decode_response
from .session import SessionWriter, capture_frames
from .transport import LinkFaults, VirtualSerialTransport


class SimulationRequest(BaseModel):
    sample_rate_hz: int = Field(250, ge=50, le=1000)
    heart_rate_bpm: float = Field(72.0, ge=35, le=220)
    target_forces: list[int] = Field(default_factory=lambda: [40, 80, 120], min_length=1, max_length=10)
    stabilize_s: float = Field(0.8, ge=0, le=10)
    acquire_s: float = Field(5.0, ge=3, le=60)


class D2ExperimentRequest(BaseModel):
    target_forces_au: list[float] = Field(default_factory=lambda: [40, 80, 120], min_length=1, max_length=20)
    sample_rate_hz: int = Field(250, ge=50, le=1000)
    heart_rate_bpm: float = Field(72, ge=35, le=220)
    seed: int = 20260805
    expired_calibration: bool = False
    never_stable_step: int | None = Field(None, ge=0)
    clipping_raw: float | None = Field(None, gt=0)
    motion_start_s: float | None = Field(None, ge=0)


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or Path(os.environ.get("PULSE_DATA_ROOT", "sessions"))
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Adaptive Radial Pulse API", version="0.2.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(create_m1_router(root))
    app.include_router(create_d3_router(root))

    @app.exception_handler(M1AppError)
    async def m1_app_exception_handler(_: Request, exc: M1AppError):
        http = app_error_to_http(exc)
        return JSONResponse(status_code=http.status_code, content={"detail": http.detail})

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        # M1-P3C 要求稳定 sanitized error envelope；非 /api/m1 路径保持 FastAPI 默认结构
        if request.url.path.startswith("/api/m1"):
            return JSONResponse(
                status_code=422,
                content={"detail": error_envelope("invalid_request", "Request validation failed.")},
            )
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "stage": "D3", "medical_use": False}

    @app.get("/api/device/d1-demo")
    def d1_demo(fragment_size: int = 7):
        """Run the production command protocol over a fragmented virtual link."""
        host, device = VirtualSerialTransport.pair(
            LinkFaults(max_chunk_size=max(1, min(fragment_size, 128))),
            LinkFaults(max_chunk_size=max(1, min(fragment_size, 128))),
        )
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        exchanges = []
        for command in (CommandCode.HELLO, CommandCode.CAPABILITIES, CommandCode.START, CommandCode.STOP):
            request_id = client.send(command)
            firmware.poll()
            response = decode_response(client.receive_frames()[0])
            exchanges.append({
                "command": command.name,
                "request_id": request_id,
                "status": response.status.name,
                "error": response.error.name,
                "data": response.data,
            })
        return {
            "transport": "virtual_serial",
            "fragment_size": fragment_size,
            "connected": host.connected,
            "exchanges": exchanges,
            "final_state": firmware.state.name,
        }

    @app.post("/api/sessions/simulate")
    def simulate(request: SimulationRequest):
        simulator = DeviceSimulator(SimulationConfig(sample_rate_hz=request.sample_rate_hz, heart_rate_bpm=request.heart_rate_bpm))
        profile = tuple(PressureStep(force, request.stabilize_s, request.acquire_s) for force in request.target_forces)
        path, manifest = capture_frames(root, simulator.frames(profile), {"source_type": "simulator", **request.model_dump()})
        report = process_session(path, request.sample_rate_hz)
        return {"manifest": manifest, "report": report}

    @app.post("/api/experiments/d2/run")
    def d2_run(request: D2ExperimentRequest):
        now = datetime.now(timezone.utc)
        calibration = CalibrationRecord(
            calibration_id=f"synthetic-force-{request.seed}", channel="force", model_type=CalibrationModel.AFFINE,
            raw_points=(0.0, 200000.0), engineering_points=(0.0, 200.0), unit="force_au",
            created_at_utc=now.isoformat(), valid_from_utc=(now - timedelta(days=1)).isoformat(),
            valid_until_utc=(now - timedelta(seconds=1)).isoformat() if request.expired_calibration else (now + timedelta(days=1)).isoformat(),
        ).signed()
        profile = PressureProfile(
            profile_id=f"d2-{request.seed}", seed=request.seed,
            steps=tuple(D2PressureStep(force, acquire_s=4.0) for force in request.target_forces_au),
        )
        faults = D2FaultConfig(
            never_stable_step=request.never_stable_step, clipping_raw=request.clipping_raw,
            motion_start_s=request.motion_start_s, motion_duration_s=1.0 if request.motion_start_s is not None else 0.0,
        )
        report = run_d2_experiment(profile, calibration, request.sample_rate_hz, request.heart_rate_bpm, faults)
        experiment_path = root / "d2-experiments" / report["report_sha256"]
        experiment_path.mkdir(parents=True, exist_ok=True)
        (experiment_path / "calibration.json").write_text(json.dumps(calibration.canonical() | {"checksum": calibration.checksum}, ensure_ascii=False, indent=2), encoding="utf-8")
        (experiment_path / "request.json").write_text(request.model_dump_json(indent=2), encoding="utf-8")
        (experiment_path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @app.get("/api/experiments/d2/{report_sha256}")
    def d2_report(report_sha256: str):
        if len(report_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in report_sha256):
            raise HTTPException(400, "invalid report id")
        path = root / "d2-experiments" / report_sha256 / "report.json"
        if not path.exists():
            raise HTTPException(404, "D2 report not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/experiments/d2/{report_sha256}/replay")
    def d2_replay(report_sha256: str):
        experiment_path = root / "d2-experiments" / report_sha256
        if not experiment_path.exists():
            raise HTTPException(404, "D2 experiment not found")
        request = D2ExperimentRequest.model_validate_json((experiment_path / "request.json").read_text(encoding="utf-8"))
        raw_calibration = json.loads((experiment_path / "calibration.json").read_text(encoding="utf-8"))
        raw_calibration["model_type"] = CalibrationModel(raw_calibration["model_type"])
        raw_calibration["raw_points"] = tuple(raw_calibration["raw_points"])
        raw_calibration["engineering_points"] = tuple(raw_calibration["engineering_points"])
        calibration = CalibrationRecord(**raw_calibration)
        profile = PressureProfile(profile_id=f"d2-{request.seed}", seed=request.seed,
                                  steps=tuple(D2PressureStep(force, acquire_s=4.0) for force in request.target_forces_au))
        faults = D2FaultConfig(never_stable_step=request.never_stable_step, clipping_raw=request.clipping_raw,
                               motion_start_s=request.motion_start_s,
                               motion_duration_s=1.0 if request.motion_start_s is not None else 0.0)
        replayed = run_d2_experiment(profile, calibration, request.sample_rate_hz, request.heart_rate_bpm, faults)
        return {"identical": replayed["report_sha256"] == report_sha256, "report": replayed}

    @app.get("/api/sessions")
    def sessions():
        result = []
        for manifest in sorted(root.glob("*/manifest.json"), reverse=True):
            result.append(__import__("json").loads(manifest.read_text(encoding="utf-8")))
        return result

    @app.get("/api/sessions/{session_id}/report")
    def report(session_id: str):
        path = root / session_id / "processed" / "report.json"
        if not path.exists():
            raise HTTPException(404, "session report not found")
        return __import__("json").loads(path.read_text(encoding="utf-8"))

    @app.websocket("/ws/simulate")
    async def simulation_stream(websocket: WebSocket):
        await websocket.accept()
        try:
            request = SimulationRequest.model_validate(await websocket.receive_json())
            simulator = DeviceSimulator(SimulationConfig(sample_rate_hz=request.sample_rate_hz, heart_rate_bpm=request.heart_rate_bpm))
            profile = tuple(PressureStep(force, request.stabilize_s, request.acquire_s) for force in request.target_forces)
            writer = SessionWriter(root, {"source_type": "simulator", **request.model_dump()})
            batch = []
            for sample, frame in zip(simulator.samples(profile), simulator.frames(profile)):
                writer.append(frame)
                batch.append({"t": sample.device_time_us, "pulse": sample.pulse_raw, "force": sample.force_raw, "target": sample.target_force, "state": sample.device_state.name})
                if len(batch) == 25:
                    await websocket.send_json({"type": "samples", "data": batch})
                    batch = []
            if batch:
                await websocket.send_json({"type": "samples", "data": batch})
            manifest = writer.close()
            report_data = process_session(writer.path, request.sample_rate_hz)
            await websocket.send_json({"type": "complete", "manifest": manifest, "report": report_data})
            await websocket.close()
        except WebSocketDisconnect:
            if "writer" in locals():
                writer.close(completed=False)
        except Exception as exc:
            if "writer" in locals():
                writer.close(completed=False)
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011)

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("digital_pulse.api:app", host="127.0.0.1", port=8000, reload=False)
