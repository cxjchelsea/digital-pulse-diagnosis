import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from digital_pulse.api import create_app


def test_d3_run_query_events_and_replay(tmp_path):
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/experiments/d3/run",
        json={"case_ids": ["emergency-stop", "host-timeout"], "seed": 7},
    )
    assert created.status_code == 200
    report = created.json()
    report_id = report["report_sha256"]
    assert report["summary"]["all_passed"]
    assert client.get(f"/api/experiments/d3/{report_id}").json() == report
    events = client.get(f"/api/experiments/d3/{report_id}/events").json()
    assert len(events["events"]) == 2
    replay = client.post(f"/api/experiments/d3/{report_id}/replay").json()
    assert replay["identical"] is True
    assert replay["report"] == report


def test_d3_unknown_case_is_422(tmp_path):
    response = TestClient(create_app(tmp_path)).post(
        "/api/experiments/d3/run", json={"case_ids": ["missing"]},
    )
    assert response.status_code == 422


def test_d3_invalid_and_missing_report_ids_are_distinct(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/experiments/d3/not-a-hash").status_code == 400
    assert client.get(f"/api/experiments/d3/{'0' * 64}").status_code == 404


def _wait_run(client, run_id: str, predicate, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/experiments/d3/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.005)
    raise AssertionError(f"timeout waiting for run; last={last}")


def test_d3_runtime_create_and_query(tmp_path):
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/experiments/d3/runs",
        json={"targets_au": [20.0, 40.0], "seed": 11, "max_duration_s": 30},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["run_id"]
    assert body["seed"] == 11
    queried = client.get(f"/api/experiments/d3/runs/{body['run_id']}")
    assert queried.status_code == 200
    assert queried.json()["run_id"] == body["run_id"]
    assert "actual_force_au" in queried.json()
    assert "position_au" in queried.json()
    assert "command" in queried.json()
    assert "tick" in queried.json()


def test_d3_runtime_abort_enters_retract_and_unloads(tmp_path):
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/experiments/d3/runs",
        json={
            "targets_au": [40.0],
            "seed": 20260805,
            "max_duration_s": 40,
            "hold": True,
        },
    ).json()
    run_id = created["run_id"]
    active = _wait_run(
        client,
        run_id,
        lambda s: s["state"] == "ACQUIRE" and s["status"] == "RUNNING",
    )
    abort = client.post(f"/api/experiments/d3/runs/{run_id}/abort")
    assert abort.status_code == 200
    abort_body = abort.json()
    assert abort_body["status"] == "ABORTING"
    after = _wait_run(
        client,
        run_id,
        lambda s: s["state"] in {"RETRACT", "IDLE"} or s["status"] == "ABORTED_IDLE",
    )
    assert after["state"] in {"RETRACT", "IDLE"}
    final = _wait_run(
        client,
        run_id,
        lambda s: s["status"] == "ABORTED_IDLE" or s["state"] == "IDLE",
    )
    assert final["state"] == "IDLE"
    assert final["status"] == "ABORTED_IDLE"
    assert final["unload_complete"] is True
    assert final["final_state"] == "IDLE"
    events = client.get(f"/api/experiments/d3/runs/{run_id}/events").json()
    abort_events = [e for e in events["events"] if e.get("type") == "ABORT"]
    assert abort_events
    assert abort_events[0]["command"] <= 0.0
    assert active["tick"] <= final["tick"]
    report = final["report"]
    assert report is not None
    assert report["positive_command_after_abort"] is False
    assert report["max_command_after_abort"] is not None
    assert report["max_command_after_abort"] <= 0.0
    assert report["positive_command_ticks_after_abort"] == []
    assert report["abort_processed_tick"] is not None
    assert report["abort_request_tick"] is not None
    assert report["retract_tick"] is not None
    assert report["idle_tick"] is not None
    assert report["unload_duration_s"] is not None
    assert report["config_hashes"]["combined_sha256"]


def test_d3_runtime_abort_idempotent(tmp_path):
    client = TestClient(create_app(tmp_path))
    run_id = client.post(
        "/api/experiments/d3/runs",
        json={"targets_au": [40.0], "seed": 3, "max_duration_s": 40, "hold": True},
    ).json()["run_id"]
    _wait_run(client, run_id, lambda s: s["state"] == "ACQUIRE")
    first = client.post(f"/api/experiments/d3/runs/{run_id}/abort")
    second = client.post(f"/api/experiments/d3/runs/{run_id}/abort")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]


def test_d3_runtime_missing_run_is_404(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get(f"/api/experiments/d3/runs/{'a' * 32}").status_code == 404
    assert client.post(f"/api/experiments/d3/runs/{'a' * 32}/abort").status_code == 404


def test_d3_runtime_invalid_run_id_is_400(tmp_path):
    client = TestClient(create_app(tmp_path))
    # Path normalization may yield 404; non-hex ids must not be accepted as runs.
    path_probe = client.get("/api/experiments/d3/runs/../etc/passwd")
    assert path_probe.status_code in {400, 404}
    assert client.get("/api/experiments/d3/runs/not-hex").status_code == 400


def test_d3_runtime_abort_after_finish_is_409(tmp_path):
    client = TestClient(create_app(tmp_path))
    run_id = client.post(
        "/api/experiments/d3/runs",
        json={"targets_au": [20.0], "seed": 5, "acquire_s": 0.2, "max_duration_s": 40},
    ).json()["run_id"]
    _wait_run(
        client,
        run_id,
        lambda s: s["status"] in {"COMPLETED", "ABORTED_IDLE", "FAILED", "FAULT_LATCHED"},
        timeout_s=15.0,
    )
    conflict = client.post(f"/api/experiments/d3/runs/{run_id}/abort")
    assert conflict.status_code == 409


def test_d3_runtime_finished_session_remains_queryable(tmp_path):
    client = TestClient(create_app(tmp_path))
    run_id = client.post(
        "/api/experiments/d3/runs",
        json={"targets_au": [20.0], "seed": 9, "acquire_s": 0.2, "max_duration_s": 40},
    ).json()["run_id"]
    final = _wait_run(
        client,
        run_id,
        lambda s: s["status"] == "COMPLETED",
        timeout_s=15.0,
    )
    again = client.get(f"/api/experiments/d3/runs/{run_id}")
    assert again.status_code == 200
    assert again.json()["status"] == "COMPLETED"
    assert again.json()["report"] is not None
    assert again.json()["report"]["report_sha256"]
    assert final["unload_complete"] is True


def test_d3_runtime_records_positive_command_after_abort(monkeypatch, tmp_path):
    from digital_pulse.d3_contracts import D3State
    from digital_pulse.d3_runtime import D3RuntimeRegistry
    from digital_pulse.d3_state_machine import D3DeviceStateMachine, StateMachineOutput

    registry = D3RuntimeRegistry()
    session = registry.create(targets=(40.0,), seed=1, hold=True, max_duration_s=40)
    session.wait_until(lambda s: s["state"] == "ACQUIRE", timeout_s=10.0)

    real_step = D3DeviceStateMachine.step
    calls = {"n": 0}

    def patched(self, inputs=None, *, requested_command=0.0, command=None):
        out = real_step(self, inputs, requested_command=requested_command, command=command)
        if session._abort_processed_tick is not None:
            calls["n"] += 1
            if calls["n"] == 2:
                return StateMachineOutput(
                    tick=out.tick,
                    device_time_us=out.device_time_us,
                    previous_state=out.previous_state,
                    state=D3State.RETRACT,
                    command=0.25,
                    safety_event=None,
                    detected_faults=(),
                )
        return out

    monkeypatch.setattr(D3DeviceStateMachine, "step", patched)
    session.request_abort()
    final = session.wait_until(
        lambda s: s["status"] in {"ABORTED_IDLE", "FAILED", "FAULT_LATCHED"} or s["state"] == "IDLE",
        timeout_s=10.0,
    )
    session.join(timeout_s=5.0)
    report = final["report"]
    assert report is not None
    assert report["positive_command_after_abort"] is True
    assert report["max_command_after_abort"] > 0.0
    assert report["positive_command_ticks_after_abort"]
    # Repeat abort must not clear violation evidence.
    again = session.request_abort()
    assert again["report"]["positive_command_after_abort"] is True
