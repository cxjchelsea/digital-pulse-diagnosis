"""Formal M1-P3C API acceptance exercised through FastAPI HTTP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from fastapi.testclient import TestClient

from digital_pulse.api import create_app
from digital_pulse.m1_app import AppSessionLoader, ReplayAnalysisService
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario


ACCEPTANCE_VERSION = "m1-p3c-acceptance-v1"
EXPECTED_API_VERSION = "m1-p3c-api-v1"


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    passed: bool
    evidence: Mapping[str, Any]


def _record(root: Path, scenario_id: str, *, seed: int):
    config = get_scenario(scenario_id, duration_s=8.0, random_seed=seed, sample_rate_hz=250.0)
    recorded = M1SessionRecorder(software_commit_sha="c" * 40).record(SimulatorDataSource(config), output_root=root)
    AppSessionLoader(root).register(recorded.session_id)
    return recorded


def _gate(name: str, passed: bool, **evidence: Any) -> Gate:
    return Gate(name=name, passed=passed, evidence=evidence)


def run_m1_p3c_acceptance(
    *,
    software_commit_sha: str = "0" * 40,
    workspace_clean: bool = True,
    frozen_baselines: Mapping[str, Any] | None = None,
    d3_regression_passed: bool = True,
    m1_p1_regression_passed: bool = True,
    m1_p2_regression_passed: bool = True,
    m1_p3b_regression_passed: bool = True,
) -> dict[str, Any]:
    gates: list[Gate] = []
    with TemporaryDirectory(prefix="m1-p3c-acceptance-") as tmp:
        root = Path(tmp)
        recorded = _record(root, "normal_high_quality", seed=3701)
        weak = _record(root, "weak_signal", seed=3702)
        client = TestClient(create_app(data_root=root))

        app_manifest = recorded.session_path / "app" / "manifest.json"
        before = app_manifest.read_bytes()
        sessions = client.get("/api/m1/sessions")
        detail = client.get(f"/api/m1/sessions/{recorded.session_id}")
        read_only_replay = client.post(f"/api/m1/sessions/{recorded.session_id}/replay", json={"software_commit_sha": software_commit_sha})
        after_read_only = app_manifest.read_bytes()
        persisted = client.post(
            f"/api/m1/sessions/{recorded.session_id}/replay",
            json={"persist": True, "run_id": "run-p3c-acceptance-001", "software_commit_sha": software_commit_sha},
        )
        runs = client.get(f"/api/m1/sessions/{recorded.session_id}/runs")
        run_detail = client.get(f"/api/m1/sessions/{recorded.session_id}/runs/run-p3c-acceptance-001")
        analysis = client.get(f"/api/m1/sessions/{recorded.session_id}/analysis")
        channels = client.get(f"/api/m1/sessions/{recorded.session_id}/channels?run_id=run-p3c-acceptance-001&max_points=7")
        duplicate = client.post(
            f"/api/m1/sessions/{recorded.session_id}/replay",
            json={"persist": True, "run_id": "run-p3c-acceptance-001", "software_commit_sha": software_commit_sha},
        )
        missing_run = client.get(f"/api/m1/sessions/{recorded.session_id}/runs/missing-run")
        traversal = client.get("/api/m1/sessions/%2e%2e")
        windows = client.get("/api/m1/sessions/C:%5Ctmp")
        quality = client.post(f"/api/m1/sessions/{weak.session_id}/replay", json={"software_commit_sha": software_commit_sha})

        gates.extend(
            [
                _gate(
                    "http_surface_available",
                    all(item.status_code == 200 for item in (sessions, detail, read_only_replay, persisted, runs, run_detail, analysis, channels)),
                    endpoints=[
                        "/api/m1/sessions",
                        "/api/m1/sessions/{session_id}",
                        "/api/m1/sessions/{session_id}/channels",
                        "/api/m1/sessions/{session_id}/analysis",
                        "/api/m1/sessions/{session_id}/runs",
                        "/api/m1/sessions/{session_id}/runs/{run_id}",
                        "/api/m1/sessions/{session_id}/replay",
                    ],
                ),
                _gate(
                    "read_only_get_and_default_replay_do_not_mutate",
                    before == after_read_only and read_only_replay.json().get("persisted") is False,
                    manifest_sha_before=before.hex()[:32],
                    manifest_sha_after=after_read_only.hex()[:32],
                ),
                _gate(
                    "persisted_replay_and_conflict_mapping",
                    persisted.status_code == 200
                    and persisted.json().get("persisted") is True
                    and duplicate.status_code == 409
                    and duplicate.json().get("detail", {}).get("error", {}).get("code") == "artifact_conflict",
                    duplicate_status=duplicate.status_code,
                ),
                _gate(
                    "analysis_safety_semantics_visible",
                    analysis.status_code == 200
                    and analysis.json()["analysis"].get("formal_parameters") is None
                    and analysis.json()["analysis"]["gate"].get("formal_parameters_allowed") is False
                    and "synthetic_only" in analysis.json()["analysis"].get("limitations", [])
                    and "pending_h1_calibration" in analysis.json()["analysis"].get("limitations", []),
                    limitations=analysis.json().get("analysis", {}).get("limitations"),
                ),
                _gate(
                    "quality_failure_is_not_http_failure",
                    quality.status_code == 200
                    and quality.json()["analysis"]["gate"].get("analysis_allowed") is False
                    and "quality_weak_signal" in quality.json()["analysis"]["gate"].get("blocking_codes", []),
                    status=quality.status_code,
                ),
                _gate(
                    "path_and_run_errors_are_stable",
                    traversal.status_code == 400
                    and windows.status_code == 400
                    and missing_run.status_code == 404
                    and traversal.json()["detail"]["error"]["code"] == "invalid_session_id"
                    and windows.json()["detail"]["error"]["code"] == "invalid_session_id"
                    and missing_run.json()["detail"]["error"]["code"] == "run_not_found",
                    traversal=traversal.json(),
                    windows=windows.json(),
                    missing_run=missing_run.json(),
                ),
                _gate(
                    "api_version_is_separate_from_app_sp_versions",
                    sessions.json().get("api_version") == EXPECTED_API_VERSION
                    and persisted.json().get("api_version") == EXPECTED_API_VERSION,
                    api_version=sessions.json().get("api_version"),
                ),
            ]
        )

    frozen = dict(frozen_baselines or {})
    gates.extend(
        [
            _gate("workspace_clean", workspace_clean, workspace_clean=workspace_clean),
            _gate("d3_regression_passed", d3_regression_passed, source="prior_acceptance"),
            _gate("m1_p1_regression_passed", m1_p1_regression_passed, source="prior_acceptance"),
            _gate("m1_p2_regression_passed", m1_p2_regression_passed, source="prior_acceptance"),
            _gate("m1_p3b_regression_passed", m1_p3b_regression_passed, source="prior_acceptance"),
            _gate(
                "p0_p1_p2_p3b_frozen",
                all(item.get("state") == "unchanged" for item in frozen.values()) if frozen else True,
                frozen_baselines=frozen,
            ),
        ]
    )
    failed = [gate.name for gate in gates if not gate.passed]
    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "acceptance": not failed,
        "failed_gates": failed,
        "software_commit_sha": software_commit_sha,
        "api_version": EXPECTED_API_VERSION,
        "gates": [asdict(gate) for gate in gates],
        "http_testclient_exercised": True,
        "report_api_present": False,
        "formal_parameters_allowed": False,
        "formal_parameters": None,
        "limitations_required": ["synthetic_only", "pending_h1_calibration"],
    }
