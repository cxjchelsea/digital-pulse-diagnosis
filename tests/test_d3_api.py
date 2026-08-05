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
