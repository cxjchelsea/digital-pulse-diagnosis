from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from digital_pulse.m1_contracts import M1ContractError, validate_json_schema


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "m1"

EXAMPLE_SCHEMA = {
    "sample-simulator-normal.json": "m1-sample.schema.json",
    "session-simulator-normal.json": "m1-session.schema.json",
    "quality-acceptable.json": "m1-quality.schema.json",
    "quality-invalid.json": "m1-quality.schema.json",
    "decision-accept.json": "m1-decision.schema.json",
    "decision-retry.json": "m1-decision.schema.json",
    "report-complete-synthetic.json": "m1-report.schema.json",
    "report-blocked-synthetic.json": "m1-report.schema.json",
}


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class M1SchemaTests(unittest.TestCase):
    def test_all_examples_pass_schema(self):
        for filename, schema_name in EXAMPLE_SCHEMA.items():
            with self.subTest(filename=filename):
                validate_json_schema(load_example(filename), schema_name)

    def test_missing_required_field_fails(self):
        payload = load_example("sample-simulator-normal.json")
        del payload["session_id"]
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-sample.schema.json")

    def test_unknown_field_fails(self):
        payload = load_example("sample-simulator-normal.json")
        payload["diagnosis"] = "forbidden"
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-sample.schema.json")

    def test_illegal_enum_fails(self):
        payload = load_example("decision-accept.json")
        payload["action"] = "diagnose_disease"
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-decision.schema.json")

    def test_illegal_time_format_fails(self):
        payload = load_example("sample-simulator-normal.json")
        payload["host_received_at_utc"] = "yesterday"
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-sample.schema.json")

    def test_negative_range_fails(self):
        payload = load_example("sample-simulator-normal.json")
        payload["device_time_us"] = -1
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-sample.schema.json")

    def test_disallowed_null_raw_when_required_by_presence_is_runtime_not_schema(self):
        # Schema allows null raw values; Python contract enforces status/value pairing.
        payload = load_example("sample-simulator-normal.json")
        payload["pulse"]["value"] = None
        validate_json_schema(payload, "m1-sample.schema.json")

    def test_absolute_path_rejected(self):
        payload = load_example("session-simulator-normal.json")
        payload = copy.deepcopy(payload)
        payload["files"][0]["relative_path"] = "C:/tmp/raw_frames.bin"
        with self.assertRaises(M1ContractError):
            validate_json_schema(payload, "m1-session.schema.json")

    def test_schemas_load_offline(self):
        for schema_name in {
            "m1-sample.schema.json",
            "m1-session.schema.json",
            "m1-quality.schema.json",
            "m1-decision.schema.json",
            "m1-report.schema.json",
        }:
            validate_json_schema(load_example(next(k for k, v in EXAMPLE_SCHEMA.items() if v == schema_name)), schema_name)


if __name__ == "__main__":
    unittest.main()
