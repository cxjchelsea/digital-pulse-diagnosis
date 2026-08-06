from __future__ import annotations

import json
from pathlib import Path
import unittest

from digital_pulse.m1_contracts import (
    M1ContractError,
    SourceType,
    configuration_digest,
    data_sample_to_m1_sample,
    dumps_stable,
    from_dict_decision,
    from_dict_quality,
    from_dict_report,
    from_dict_sample,
    from_dict_session,
    parse_contract,
)
from digital_pulse.protocol import DataSample, DeviceState, StatusFlag, encode_data_frame, decode_frame


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "m1"


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class M1ContractTests(unittest.TestCase):
    def test_examples_round_trip_through_python_contracts(self):
        cases = (
            ("sample", "sample-simulator-normal.json", from_dict_sample),
            ("session", "session-simulator-normal.json", from_dict_session),
            ("quality", "quality-acceptable.json", from_dict_quality),
            ("quality", "quality-invalid.json", from_dict_quality),
            ("decision", "decision-accept.json", from_dict_decision),
            ("decision", "decision-retry.json", from_dict_decision),
            ("report", "report-complete-synthetic.json", from_dict_report),
            ("report", "report-blocked-synthetic.json", from_dict_report),
        )
        for kind, filename, loader in cases:
            with self.subTest(filename=filename):
                original = load_example(filename)
                obj = loader(original)
                obj.validate()
                obj.validate_schema()
                restored = parse_contract(kind, json.loads(obj.to_json()))
                self.assertEqual(json.loads(restored.to_json()), original)
        quality_ok = from_dict_quality(load_example("quality-acceptable.json"))
        report_blocked = from_dict_report(load_example("report-blocked-synthetic.json"))
        self.assertIsNone(quality_ok.confidence)
        self.assertFalse(report_blocked.analysis_allowed)

    def test_stable_serialization(self):
        first = from_dict_sample(load_example("sample-simulator-normal.json"))
        second = from_dict_sample(load_example("sample-simulator-normal.json"))
        self.assertEqual(dumps_stable(first), dumps_stable(second))

    def test_connected_channel_rejects_null_and_absent_rejects_zero(self):
        payload = load_example("sample-simulator-normal.json")
        payload["pulse"]["value"] = None
        with self.assertRaisesRegex(M1ContractError, "connected"):
            from_dict_sample(payload)
        payload = load_example("sample-simulator-normal.json")
        payload["pulse"]["status"] = "disconnected"
        payload["pulse"]["value"] = 0
        with self.assertRaisesRegex(M1ContractError, "null"):
            from_dict_sample(payload)

    def test_simulator_requires_provenance_hardware_forbids_it(self):
        payload = load_example("session-simulator-normal.json")
        payload["random_seed"] = None
        with self.assertRaisesRegex(M1ContractError, "simulator"):
            from_dict_session(payload)
        payload = load_example("session-simulator-normal.json")
        payload["source_type"] = "hardware"
        payload["simulator_version"] = None
        payload["scenario_id"] = None
        payload["random_seed"] = None
        payload["device_id"] = "esp32-s3-001"
        from_dict_session(payload).validate()
        payload["scenario_id"] = "should-not-exist"
        with self.assertRaisesRegex(M1ContractError, "hardware"):
            from_dict_session(payload)

    def test_incomplete_session_requires_reason(self):
        payload = load_example("session-simulator-normal.json")
        payload["completed"] = False
        payload["completion_reason"] = None
        with self.assertRaisesRegex(M1ContractError, "completion_reason"):
            from_dict_session(payload)

    def test_pending_calibration_forbids_confidence(self):
        payload = load_example("quality-acceptable.json")
        payload["confidence"] = 0.99
        with self.assertRaisesRegex(M1ContractError, "confidence"):
            from_dict_quality(payload)

    def test_analysis_allowed_false_blocks_formal_parameters(self):
        payload = load_example("report-blocked-synthetic.json")
        payload["objective_parameters"] = {"heart_rate_bpm": 70.0}
        with self.assertRaisesRegex(M1ContractError, "analysis_allowed"):
            from_dict_report(payload)

    def test_i1_rejects_future_actions_and_retry_bounds(self):
        payload = load_example("decision-accept.json")
        payload["action"] = "adjust_pressure"
        payload["reason_codes"] = ["reserved_future_action"]
        with self.assertRaisesRegex(M1ContractError, "I1"):
            from_dict_decision(payload)
        payload = load_example("decision-retry.json")
        payload["retry_count"] = 4
        payload["max_retry_count"] = 3
        with self.assertRaisesRegex(M1ContractError, "retry_count"):
            from_dict_decision(payload)

    def test_abort_cannot_be_triggered_by_ordinary_quality_alone(self):
        payload = load_example("decision-accept.json")
        payload["action"] = "abort_and_release"
        payload["reason_codes"] = ["weak_signal"]
        with self.assertRaisesRegex(M1ContractError, "abort"):
            from_dict_decision(payload)
        payload["reason_codes"] = ["emergency_stop"]
        from_dict_decision(payload).validate()

    def test_data_sample_adapter_preserves_protocol_object(self):
        original = DataSample(
            frame_sequence=9,
            device_time_us=9000,
            sample_sequence=9,
            pulse_raw=111,
            force_raw=222,
            reference_raw=333,
            motor_position=0,
            target_force=0,
            device_state=DeviceState.ACQUIRE,
            status_flags=StatusFlag.NONE,
        )
        encoded = encode_data_frame(original)
        before = decode_frame(encoded).sample
        adapted = data_sample_to_m1_sample(
            before,
            session_id="adapter-session",
            source_type=SourceType.SIMULATOR,
            host_received_at_utc="2026-08-06T05:00:00Z",
            sequence_valid=True,
            timestamp_valid=True,
        )
        after = decode_frame(encoded).sample
        self.assertEqual(before, after)
        self.assertEqual(adapted.pulse.value, 111)
        self.assertEqual(adapted.load.value, 222)
        self.assertEqual(adapted.ppg.value, 333)
        self.assertEqual(adapted.device_state, "ACQUIRE")
        adapted.validate_schema()

    def test_adapter_maps_disconnect_without_zero_as_missing(self):
        sample = DataSample(
            frame_sequence=1,
            device_time_us=1000,
            sample_sequence=1,
            pulse_raw=0,
            force_raw=0,
            reference_raw=10,
            motor_position=0,
            target_force=0,
            device_state=DeviceState.FAULT,
            status_flags=StatusFlag.SENSOR_DISCONNECTED,
        )
        adapted = data_sample_to_m1_sample(
            sample,
            session_id="adapter-disconnect",
            host_received_at_utc="2026-08-06T05:00:00Z",
        )
        self.assertIsNone(adapted.pulse.value)
        self.assertIsNone(adapted.load.value)
        self.assertEqual(adapted.pulse.status.value, "disconnected")
        self.assertIn("sensor_disconnected", adapted.fault_flags)

    def test_configuration_digest_is_stable(self):
        digest = configuration_digest({"scenario_id": "simulator-normal", "sample_rate_hz": 250})
        self.assertEqual(digest, "a40d869fd9ba17bf31ecc5080379cc8a684318db60d73ccd11d4711c5f921255")
        self.assertEqual(len(digest), 64)

    def test_report_requires_medical_limitation(self):
        payload = load_example("report-complete-synthetic.json")
        payload["limitations"] = ["synthetic_input", "pending_h1_calibration", "not_hardware_validated"]
        with self.assertRaisesRegex(M1ContractError, "not_for_medical_use"):
            from_dict_report(payload)


if __name__ == "__main__":
    unittest.main()
