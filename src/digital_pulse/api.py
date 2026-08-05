"""FastAPI application for simulated P0 acquisition and analysis."""

from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .device import DeviceSimulator, PressureStep, SimulationConfig
from .firmware import DeviceClient, FirmwareSimulator
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


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or Path(os.environ.get("PULSE_DATA_ROOT", "sessions"))
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Adaptive Radial Pulse API", version="0.2.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return {"status": "ok", "stage": "D1", "medical_use": False}

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
