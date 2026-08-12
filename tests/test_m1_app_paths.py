from __future__ import annotations

from pathlib import Path

import pytest

from digital_pulse.m1_app import M1AppError, SafeSessionPath


@pytest.mark.parametrize(
    "value",
    [
        "../secret",
        "../../secret",
        "C:\\secret",
        "D:/secret",
        "\\\\server\\share",
        "/absolute/path",
        "..\\secret",
        "nested/..\\secret",
        "nested\\file.json",
        "./file.json",
        "C:",
        "C:relative",
        "C::",
        "CON",
        "NUL.txt",
        "PRN",
        "AUX",
        "COM1",
        "LPT1.log",
        "trailing-dot.",
        "trailing-space ",
        "bad<name>.json",
    ],
)
def test_cross_platform_escape_forms_are_rejected(tmp_path: Path, value: str):
    safe = SafeSessionPath(tmp_path)
    with pytest.raises(M1AppError) as caught:
        safe.resolve(value, asset="test_asset")
    assert caught.value.code == "path_escape"
    assert str(tmp_path) not in str(caught.value)


def test_valid_nested_posix_path_resolves_inside_root(tmp_path: Path):
    target = tmp_path / "app" / "runs" / "run-1" / "analysis.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    resolved = SafeSessionPath(tmp_path).resolve(
        "app/runs/run-1/analysis.json",
        asset="analysis",
        require_exists=True,
        require_file=True,
    )
    assert resolved == target.resolve()


def test_symlink_escape_is_rejected(tmp_path: Path):
    session_root = tmp_path / "session"
    outside = tmp_path / "outside"
    session_root.mkdir()
    outside.mkdir()
    (outside / "secret.json").write_text("{}", encoding="utf-8")
    link = session_root / "safe-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(M1AppError) as caught:
        SafeSessionPath(session_root).resolve(
            "safe-link/secret.json",
            asset="analysis",
            require_exists=True,
        )
    assert caught.value.code == "symlink_escape"


def test_percent_encoded_parent_is_literal_until_a_future_api_decodes_it(tmp_path: Path):
    resolved = SafeSessionPath(tmp_path).resolve("foo/%2e%2e/bar", asset="analysis")
    assert resolved == (tmp_path / "foo" / "%2e%2e" / "bar").resolve()


def test_session_root_replacement_with_symlink_is_rejected(tmp_path: Path):
    session_root = tmp_path / "session"
    held_root = tmp_path / "held-session"
    outside = tmp_path / "outside"
    session_root.mkdir(); outside.mkdir()
    (outside / "secret.json").write_text("{}", encoding="utf-8")
    safe = SafeSessionPath(session_root)
    session_root.rename(held_root)
    try:
        session_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        held_root.rename(session_root)
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(M1AppError) as caught:
        safe.resolve("secret.json", asset="root_replacement", require_exists=True)
    assert caught.value.code in {"path_escape", "symlink_escape"}
