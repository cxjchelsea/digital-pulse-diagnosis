import json

import pytest

from digital_pulse.d3_experiment import D3ReportStore, run_d3_experiment


def test_default_report_contains_full_passing_matrix():
    report = run_d3_experiment()
    assert report["summary"] == {
        "case_count": 14, "passed_count": 14,
        "failed_count": 0, "all_passed": True,
    }
    assert len(report["events"]) == 14


def test_report_has_explicit_evidence_boundary():
    report = run_d3_experiment()
    assert report["model_units"] == "relative_au"
    assert report["medical_use"] is False
    assert report["analysis_allowed"] is False
    assert report["limitations"]


def test_same_request_has_identical_report_hash():
    assert run_d3_experiment()["report_sha256"] == run_d3_experiment()["report_sha256"]


def test_case_order_is_part_of_report_identity():
    a = run_d3_experiment(("emergency-stop", "host-timeout"))
    b = run_d3_experiment(("host-timeout", "emergency-stop"))
    assert a["report_sha256"] != b["report_sha256"]


def test_unknown_and_duplicate_cases_are_rejected():
    with pytest.raises(ValueError, match="unknown"):
        run_d3_experiment(("missing",))
    with pytest.raises(ValueError, match="unique"):
        run_d3_experiment(("emergency-stop", "emergency-stop"))


def test_store_writes_request_events_and_report(tmp_path):
    report = run_d3_experiment(("emergency-stop", "host-timeout"))
    path = D3ReportStore(tmp_path).save(report)
    assert (path / "request.json").exists()
    assert len((path / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads((path / "report.json").read_text(encoding="utf-8")) == report


def test_store_load_verifies_checksum(tmp_path):
    report = run_d3_experiment(("emergency-stop",))
    store = D3ReportStore(tmp_path)
    path = store.save(report)
    raw = json.loads((path / "report.json").read_text(encoding="utf-8"))
    raw["seed"] += 1
    (path / "report.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        store.load(report["report_sha256"])


def test_store_replay_is_identical(tmp_path):
    report = run_d3_experiment(("emergency-stop", "motor-stall"), seed=7)
    store = D3ReportStore(tmp_path)
    store.save(report)
    identical, replayed = store.replay(report["report_sha256"])
    assert identical
    assert replayed == report


def test_invalid_report_id_cannot_escape_store(tmp_path):
    with pytest.raises(ValueError, match="report id"):
        D3ReportStore(tmp_path).load("../../etc/passwd")


def test_missing_valid_report_returns_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        D3ReportStore(tmp_path).load("0" * 64)
