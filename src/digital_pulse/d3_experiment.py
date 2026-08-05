"""Canonical D3 fault-matrix reports and filesystem persistence."""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
import hashlib
import json
from pathlib import Path
import re

from digital_pulse.d3_fault_matrix import FaultMatrixRunner, default_fault_matrix


REPORT_ID = re.compile(r"^[0-9a-f]{64}$")


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_d3_experiment(case_ids: tuple[str, ...] | None = None, *, seed: int = 20260805) -> dict:
    cases = default_fault_matrix()
    known = {case.case_id: case for case in cases}
    selected_ids = tuple(known) if case_ids is None else tuple(case_ids)
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("case_ids must be non-empty and unique")
    unknown = [case_id for case_id in selected_ids if case_id not in known]
    if unknown:
        raise ValueError(f"unknown D3 case: {unknown[0]}")
    selected = tuple(known[case_id] for case_id in selected_ids)
    results = FaultMatrixRunner().run_all(selected)
    serialized = [_jsonable(asdict(result)) for result in results]
    events = [
        {
            "case_id": item["case_id"],
            "event_tick": item["event_tick"],
            "fault": item["detected_code"],
            "action": item["action"],
            "state": item["final_state"],
            "detection_latency_ticks": item["detection_latency_ticks"],
            "command": item["command_at_detection"],
            "detected_faults": item["detected_faults"],
        }
        for item in serialized if item["event_tick"] is not None
    ]
    payload = {
        "schema_version": "1.0.0",
        "experiment_type": "d3_fault_matrix",
        "seed": seed,
        "case_ids": list(selected_ids),
        "model_units": "relative_au",
        "medical_use": False,
        "analysis_allowed": False,
        "summary": {
            "case_count": len(results),
            "passed_count": sum(result.passed for result in results),
            "failed_count": sum(not result.passed for result in results),
            "all_passed": all(result.passed for result in results),
        },
        "results": serialized,
        "events": events,
        "limitations": [
            "Synthetic model evidence only.",
            "No real actuator, sensor, tissue, release-time or human safety claim.",
        ],
        "disclaimer": "D3 synthetic relative-unit control evidence; not medical or hardware safety validation.",
    }
    payload["report_sha256"] = _digest(payload)
    return payload


class D3ReportStore:
    def __init__(self, root: Path):
        self.root = root / "d3-experiments"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, report: dict) -> Path:
        report_id = report.get("report_sha256", "")
        canonical = dict(report)
        canonical.pop("report_sha256", None)
        if not REPORT_ID.fullmatch(report_id) or _digest(canonical) != report_id:
            raise ValueError("invalid D3 report checksum")
        path = self.root / report_id
        path.mkdir(parents=True, exist_ok=True)
        request = {"case_ids": report["case_ids"], "seed": report["seed"]}
        (path / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (path / "events.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in report["events"]),
            encoding="utf-8",
        )
        (path / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return path

    def load(self, report_id: str) -> dict:
        self._validate_id(report_id)
        path = self.root / report_id / "report.json"
        if not path.exists():
            raise FileNotFoundError(report_id)
        report = json.loads(path.read_text(encoding="utf-8"))
        canonical = dict(report)
        stored = canonical.pop("report_sha256", "")
        if stored != report_id or _digest(canonical) != report_id:
            raise ValueError("stored D3 report checksum mismatch")
        return report

    def replay(self, report_id: str) -> tuple[bool, dict]:
        original = self.load(report_id)
        replayed = run_d3_experiment(tuple(original["case_ids"]), seed=original["seed"])
        return replayed["report_sha256"] == report_id, replayed

    @staticmethod
    def _validate_id(report_id: str) -> None:
        if not REPORT_ID.fullmatch(report_id):
            raise ValueError("invalid D3 report id")
