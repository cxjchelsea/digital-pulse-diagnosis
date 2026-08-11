from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from digital_pulse.m1_app import (
    APP_PROCESSING_VERSION_P3A,
    AppAssetRole,
    AppAssetWrite,
    AppPersistence,
    AppSessionLoader,
    M1AppError,
)

from _m1_app_helpers import FIXED_TIME, provenance, record_session


def registered(root: Path):
    _, recorded = record_session(root)
    AppSessionLoader(root, clock=lambda: FIXED_TIME).register(recorded.session_id)
    return recorded


def analysis_asset() -> AppAssetWrite:
    return AppAssetWrite(
        role=AppAssetRole.ANALYSIS,
        relative_path="analysis.json",
        content=b'{"schema_version":"m1-p3-analysis-placeholder-v1"}\n',
        media_type="application/json",
        producer="m1-p3a-tests",
        version=APP_PROCESSING_VERSION_P3A,
    )


def persistence(root: Path, failpoint: str | None = None) -> AppPersistence:
    def inject(point: str) -> None:
        if point == failpoint:
            raise M1AppError("persistence_failed", "Injected persistence failure.", asset=point)

    return AppPersistence(root, clock=lambda: FIXED_TIME, failure_injector=inject if failpoint else None)


def test_successful_commit_is_visible_verified_and_immutable(tmp_path: Path):
    recorded = registered(tmp_path)
    run = persistence(tmp_path).commit_run(
        recorded.session_id,
        "run-001",
        provenance=provenance(),
        assets=(analysis_asset(),),
    )
    assert run.run_id == "run-001"
    assert {item.role for item in run.assets} == {
        AppAssetRole.ANALYSIS,
        AppAssetRole.PROVENANCE,
        AppAssetRole.CHECKSUMS,
    }
    loaded = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME).load(recorded.session_id)
    assert loaded.app_manifest.current_run_id == "run-001"
    assert [item.run_id for item in loaded.app_manifest.runs] == ["run-001"]

    run_dir = recorded.session_path / "app" / "runs" / "run-001"
    before = {path.relative_to(run_dir).as_posix(): path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-001",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "artifact_conflict"
    after = {path.relative_to(run_dir).as_posix(): path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("failpoint", ["write_asset:analysis", "hash_assets", "rename_run"])
def test_failure_before_rename_never_exposes_run(tmp_path: Path, failpoint: str):
    recorded = registered(tmp_path)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path, failpoint).commit_run(
            recorded.session_id,
            "run-before-rename",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "persistence_failed"
    loaded = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME).load(recorded.session_id)
    assert loaded.app_manifest.runs == ()
    assert not (recorded.session_path / "app" / "runs" / "run-before-rename").exists()
    assert any(item.startswith("app/.tmp/") for item in loaded.orphan_artifacts)


@pytest.mark.parametrize("failpoint", ["after_rename", "manifest_update"])
def test_failure_after_rename_leaves_unregistered_orphan_not_visible(tmp_path: Path, failpoint: str):
    recorded = registered(tmp_path)
    manifest_before = (recorded.session_path / "app" / "manifest.json").read_bytes()
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path, failpoint).commit_run(
            recorded.session_id,
            "run-orphan",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "persistence_failed"
    assert (recorded.session_path / "app" / "runs" / "run-orphan").is_dir()
    assert (recorded.session_path / "app" / "manifest.json").read_bytes() == manifest_before
    loaded = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME).load(recorded.session_id)
    assert loaded.app_manifest.runs == ()
    assert "app/runs/run-orphan" in loaded.orphan_artifacts


def test_existing_orphan_directory_is_an_artifact_conflict(tmp_path: Path):
    recorded = registered(tmp_path)
    orphan = recorded.session_path / "app" / "runs" / "run-existing"
    orphan.mkdir(parents=True)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-existing",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "artifact_conflict"


def test_tampered_committed_run_asset_is_detected_on_load(tmp_path: Path):
    recorded = registered(tmp_path)
    persistence(tmp_path).commit_run(
        recorded.session_id,
        "run-tamper",
        provenance=provenance(),
        assets=(analysis_asset(),),
    )
    path = recorded.session_path / "app" / "runs" / "run-tamper" / "analysis.json"
    path.write_bytes(b"{}\n")
    with pytest.raises(M1AppError) as caught:
        AppSessionLoader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"


def test_invalid_run_id_is_rejected_before_any_write(tmp_path: Path):
    recorded = registered(tmp_path)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "../escape",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "manifest_invalid"
    assert not (recorded.session_path / "escape").exists()


def test_concurrent_commits_serialize_manifest_read_modify_write(tmp_path: Path):
    recorded = registered(tmp_path)

    def commit(run_id: str):
        return persistence(tmp_path).commit_run(
            recorded.session_id,
            run_id,
            provenance=provenance(),
            assets=(analysis_asset(),),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = tuple(executor.map(commit, ("run-concurrent-a", "run-concurrent-b")))

    assert {item.run_id for item in runs} == {"run-concurrent-a", "run-concurrent-b"}
    loaded = AppSessionLoader(tmp_path, clock=lambda: FIXED_TIME).load(recorded.session_id)
    assert {item.run_id for item in loaded.app_manifest.runs} == {
        "run-concurrent-a",
        "run-concurrent-b",
    }
