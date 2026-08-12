from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
from pathlib import Path
import time

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


def _process_register(root, session_id, timestamp, start, queue, ready=None, release=None):
    def clock():
        if ready is not None:
            ready.set()
            release.wait(10)
        return timestamp

    start.wait(10)
    try:
        loaded = AppSessionLoader(Path(root), clock=clock).register(session_id)
        queue.put(("success", loaded.app_manifest.registered_at_utc))
    except Exception as exc:  # pragma: no cover - assertion happens in parent
        queue.put((type(exc).__name__, getattr(exc, "code", None), str(exc), getattr(exc, "asset", None)))


def _process_commit(root, session_id, run_id, start, queue):
    start.wait(10)
    try:
        persistence(Path(root)).commit_run(
            session_id,
            run_id,
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
        queue.put(("success", run_id))
    except Exception as exc:  # pragma: no cover - assertion happens in parent
        queue.put((type(exc).__name__, getattr(exc, "code", None), str(exc), getattr(exc, "asset", None)))


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


@pytest.mark.parametrize(
    "failpoint",
    [
        "after_temp_creation",
        "write_asset:analysis",
        "after_asset_write:analysis",
        "hash_assets",
        "after_checksum",
        "before_rename",
        "rename_run",
    ],
)
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


def test_failure_before_temp_creation_writes_nothing(tmp_path: Path):
    recorded = registered(tmp_path)
    with pytest.raises(M1AppError):
        persistence(tmp_path, "before_temp_creation").commit_run(
            recorded.session_id,
            "run-no-temp",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert loaded.app_manifest.runs == ()
    assert loaded.orphan_artifacts == ()


@pytest.mark.parametrize(
    "failpoint",
    ["after_rename", "before_manifest_update", "manifest_update"],
)
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


def test_failure_signal_after_manifest_update_still_has_complete_registered_run(tmp_path: Path):
    recorded = registered(tmp_path)
    with pytest.raises(M1AppError):
        persistence(tmp_path, "after_manifest_update").commit_run(
            recorded.session_id,
            "run-committed-before-signal",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert [item.run_id for item in loaded.app_manifest.runs] == [
        "run-committed-before-signal"
    ]
    assert loaded.orphan_artifacts == ()


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


@pytest.mark.parametrize("asset_name", ["analysis.json", "provenance.json", "checksums.json"])
def test_tampered_committed_run_asset_is_detected_on_load(tmp_path: Path, asset_name: str):
    recorded = registered(tmp_path)
    persistence(tmp_path).commit_run(
        recorded.session_id,
        "run-tamper",
        provenance=provenance(),
        assets=(analysis_asset(),),
    )
    path = recorded.session_path / "app" / "runs" / "run-tamper" / asset_name
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


def test_run_processing_version_must_match_manifest(tmp_path: Path):
    recorded = registered(tmp_path)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-version-mismatch",
            provenance=replace(provenance(), app_processing_version="future"),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "manifest_invalid"


def test_p3a_cannot_claim_future_execution_mode(tmp_path: Path):
    from digital_pulse.m1_app import AppExecutionMode

    recorded = registered(tmp_path)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-false-direct",
            provenance=replace(provenance(), execution_mode=AppExecutionMode.DIRECT),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    "content",
    [b'{"value":NaN}\n', b'{"value":Infinity}\n', b'{"value":-Infinity}\n', b'{"value":1,"value":2}\n'],
)
def test_invalid_domain_json_is_rejected_before_any_write(tmp_path: Path, content: bytes):
    recorded = registered(tmp_path)
    invalid = replace(analysis_asset(), content=content)
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-invalid-json",
            provenance=provenance(),
            assets=(invalid,),
        )
    assert caught.value.code == "manifest_invalid"
    assert not (recorded.session_path / "app" / "runs" / "run-invalid-json").exists()


def test_duplicate_nested_run_manifest_key_is_rejected_by_loader(tmp_path: Path):
    recorded = registered(tmp_path)
    persistence(tmp_path).commit_run(
        recorded.session_id,
        "run-duplicate-key",
        provenance=provenance(),
        assets=(analysis_asset(),),
    )
    app_manifest = recorded.session_path / "app" / "manifest.json"
    text = app_manifest.read_text(encoding="utf-8")
    marker = '"run_id":"run-duplicate-key"'
    text = text.replace(marker, '"run_id":"attacker",' + marker, 1)
    app_manifest.write_text(text, encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        AppSessionLoader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "manifest_invalid"


def test_asset_fsync_error_is_structured_and_never_publishes_run(tmp_path: Path, monkeypatch):
    recorded = registered(tmp_path)
    import digital_pulse.m1_app.persistence as persistence_module

    monkeypatch.setattr(
        persistence_module.os,
        "fsync",
        lambda _: (_ for _ in ()).throw(OSError("D:/private/fsync")),
    )
    with pytest.raises(M1AppError) as caught:
        persistence(tmp_path).commit_run(
            recorded.session_id,
            "run-fsync-failure",
            provenance=provenance(),
            assets=(analysis_asset(),),
        )
    assert caught.value.code == "persistence_failed"
    assert str(tmp_path) not in str(caught.value.to_public_dict())
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert loaded.app_manifest.runs == ()


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


@pytest.mark.parametrize("_attempt", range(5))
def test_cross_process_concurrent_registration_returns_one_snapshot(tmp_path: Path, _attempt: int):
    _, recorded = record_session(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_register,
            args=(str(tmp_path), recorded.session_id, timestamp, start, queue),
        )
        for timestamp in ("2026-08-11T02:00:01Z", "2026-08-11T02:00:02Z")
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    final = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert all(item[0] == "success" for item in outcomes), outcomes
    assert {item[1] for item in outcomes} == {final.app_manifest.registered_at_utc}


def test_cross_process_registration_serializes_with_run_commit(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event(); start.set()
    ready = context.Event(); release = context.Event(); queue = context.Queue()
    registration = context.Process(
        target=_process_register,
        args=(str(tmp_path), recorded.session_id, FIXED_TIME, start, queue, ready, release),
    )
    registration.start()
    assert ready.wait(15)
    commit = context.Process(
        target=_process_commit,
        args=(str(tmp_path), recorded.session_id, "run-after-registration", start, queue),
    )
    commit.start()
    time.sleep(0.3)
    assert commit.is_alive()
    release.set()
    registration.join(30); commit.join(30)
    assert registration.exitcode == 0 and commit.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in range(2)]
    assert all(item[0] == "success" for item in outcomes)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert [item.run_id for item in loaded.app_manifest.runs] == [
        "run-after-registration"
    ]


@pytest.mark.parametrize("same_run", [False, True])
def test_cross_process_run_lock_prevents_lost_updates_and_mixed_same_run(tmp_path: Path, same_run: bool):
    recorded = registered(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event(); queue = context.Queue()
    run_ids = ("run-process", "run-process") if same_run else ("run-process-a", "run-process-b")
    processes = [
        context.Process(
            target=_process_commit,
            args=(str(tmp_path), recorded.session_id, run_id, start, queue),
        )
        for run_id in run_ids
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    if same_run:
        assert sorted(item[0] for item in outcomes) == ["M1AppError", "success"]
        assert any(item[1] == "artifact_conflict" for item in outcomes)
        assert [item.run_id for item in loaded.app_manifest.runs] == ["run-process"]
    else:
        assert all(item[0] == "success" for item in outcomes)
        assert {item.run_id for item in loaded.app_manifest.runs} == set(run_ids)


def test_stale_lock_file_is_only_an_os_anchor(tmp_path: Path):
    recorded = registered(tmp_path)
    lock = recorded.session_path / "app" / ".commit.lock"
    assert lock.is_file()
    persistence(tmp_path).commit_run(
        recorded.session_id,
        "run-after-stale-file",
        provenance=provenance(),
        assets=(analysis_asset(),),
    )
    assert AppSessionLoader(tmp_path).load(recorded.session_id).app_manifest.current_run_id == "run-after-stale-file"
