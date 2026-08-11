"""Regression tests for frozen-contract Git baseline classification."""

from __future__ import annotations

from subprocess import CompletedProcess

import scripts.generate_m1_p2_acceptance as generator
from digital_pulse.m1_p2_acceptance import _frozen_baseline_gates


def _completed(returncode: int, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=["git"], returncode=returncode, stdout="", stderr=stderr)


def _mock_git(monkeypatch, *results: CompletedProcess[str]) -> None:
    pending = iter(results)
    monkeypatch.setattr(generator.subprocess, "run", lambda *args, **kwargs: next(pending))


def test_frozen_path_check_reports_unchanged(monkeypatch):
    _mock_git(monkeypatch, _completed(0), _completed(0))
    result = generator._check_frozen_paths("baseline", ("contract.json",))
    assert result.baseline_available is True
    assert result.state == "unchanged"


def test_frozen_path_check_reports_real_change(monkeypatch):
    _mock_git(monkeypatch, _completed(0), _completed(1))
    result = generator._check_frozen_paths("baseline", ("contract.json",))
    assert result.baseline_available is True
    assert result.state == "changed"


def test_frozen_path_check_distinguishes_missing_baseline(monkeypatch):
    _mock_git(monkeypatch, _completed(128, "Not a valid object name"))
    result = generator._check_frozen_paths("missing", ("contract.json",))
    assert result.baseline_available is False
    assert result.state == "baseline_unavailable"
    assert result.state != "changed"


def test_frozen_path_check_distinguishes_git_error(monkeypatch):
    _mock_git(monkeypatch, _completed(0), _completed(129, "fatal: usage error"))
    result = generator._check_frozen_paths("baseline", ("contract.json",))
    assert result.baseline_available is True
    assert result.state == "error"
    assert result.state != "changed"


def _gates(detail):
    return _frozen_baseline_gates(
        baseline_gate="available",
        unchanged_gate="unchanged",
        error_gate="error_free",
        detail=detail,
    )


def test_missing_baseline_fails_availability_without_claiming_drift():
    assert _gates({"available": False, "state": "baseline_unavailable"}) == {
        "available": False,
        "unchanged": True,
        "error_free": True,
    }


def test_git_error_fails_error_gate_without_claiming_drift():
    assert _gates({"available": True, "state": "error"}) == {
        "available": True,
        "unchanged": True,
        "error_free": False,
    }


def test_real_change_fails_only_unchanged_gate():
    assert _gates({"available": True, "state": "changed"}) == {
        "available": True,
        "unchanged": False,
        "error_free": True,
    }
