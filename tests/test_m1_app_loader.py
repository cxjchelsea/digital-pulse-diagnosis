from __future__ import annotations

from pathlib import Path

import pytest

from digital_pulse.m1_app import AppSessionLoader, M1AppError

from _m1_app_helpers import FIXED_TIME, record_session


def test_loader_requires_known_registered_session(tmp_path: Path):
    app_loader = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME)
    with pytest.raises(M1AppError) as missing:
        app_loader.load("does-not-exist")
    assert missing.value.code == "session_not_found"

    _, recorded = record_session(tmp_path)
    with pytest.raises(M1AppError) as unregistered:
        app_loader.load(recorded.session_id)
    assert unregistered.value.code == "manifest_invalid"


def test_loader_returns_structured_references_without_running_sp(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    app_loader = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME)
    loaded = app_loader.register(recorded.session_id)
    assert loaded.session_ref.session_id == recorded.session_id
    assert loaded.session_ref.source_type == "simulator"
    assert loaded.session_ref.raw_persistence_status == "ok"
    assert set(loaded.source_asset_paths) == {
        item.role for item in loaded.app_manifest.source_assets
    }


def test_invalid_app_manifest_fails_without_absolute_path_leak(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME).register(recorded.session_id)
    app_manifest = recorded.session_path / "app" / "manifest.json"
    app_manifest.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        AppSessionLoader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "manifest_invalid"
    assert str(tmp_path) not in str(caught.value)


def test_p3a_package_has_no_simulator_oracle_dependency():
    package = Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_app"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = (
        "ScenarioDefinition",
        "expected_quality_label",
        "expected_int_action",
        "FaultPlan",
        "expected.json",
    )
    assert all(item not in text for item in forbidden)
