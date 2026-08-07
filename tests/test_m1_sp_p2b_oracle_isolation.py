from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from digital_pulse.m1_sp.processor import SPQualityProcessor

from _m1_sp_helpers import load_replay_samples, record_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SP_ROOT = REPO_ROOT / "src" / "digital_pulse" / "m1_sp"


def _quality_fingerprint(result):
    return {
        "processing_status": result.processing_status,
        "blocking_codes": list(result.blocking_codes),
        "parameter_version": result.parameter_version,
        "processing_version": result.processing_version,
        "configuration_digest": result.configuration_digest,
        "labels": [q.label.value for q in result.quality_results],
        "reason_codes": [list(q.reason_codes) for q in result.quality_results],
        "metrics": [dict(q.metrics) for q in result.quality_results],
        "window_ids": [q.window_id for q in result.quality_results],
    }


class M1SPP2BOracleIsolationTests(unittest.TestCase):
    def test_production_sources_do_not_import_simulator_oracle(self):
        forbidden_imports = {
            "digital_pulse.m1_simulator.scenarios",
            "digital_pulse.m1_simulator.faults",
            "digital_pulse.m1_simulator.device_faults",
            "digital_pulse.m1_simulator.transport",
        }
        forbidden_names = {
            "ScenarioDefinition",
            "expected_quality_label",
            "expected_int_action",
            "FaultKind",
            "FaultWindow",
            "FaultPlan",
            "TransportFaultPlan",
            "DeviceFaultPlan",
            "PersistenceFaultPlan",
        }
        for path in SP_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_imports, path.name)
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(
                        any(module == item or module.startswith(item + ".") for item in forbidden_imports),
                        path.name,
                    )
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, path.name)
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_names, path.name)

    def test_delete_scenario_and_expected_does_not_change_p2b_result(self):
        tmp, session_path, session, samples = record_scenario(
            "normal_high_quality", duration_s=8.0, random_seed=1001
        )
        try:
            proc = SPQualityProcessor()
            before = _quality_fingerprint(proc.process(session, samples))
            (session_path / "scenario.json").unlink()
            (session_path / "expected.json").unlink()
            after = _quality_fingerprint(proc.process(session, samples))
            self.assertEqual(before, after)
            self.assertEqual(before["labels"], ["acceptable"])
        finally:
            tmp.cleanup()

    def test_tampered_expected_does_not_change_p2b_result(self):
        tmp, session_path, session, samples = record_scenario(
            "normal_high_quality", duration_s=8.0, random_seed=1001
        )
        try:
            proc = SPQualityProcessor()
            before = _quality_fingerprint(proc.process(session, samples))
            expected_path = session_path / "expected.json"
            payload = json.loads(expected_path.read_text(encoding="utf-8"))
            payload["expected_quality_label"] = "motion_artifact"
            expected_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            after = _quality_fingerprint(proc.process(session, samples))
            self.assertEqual(before, after)
            self.assertEqual(after["labels"], ["acceptable"])
        finally:
            tmp.cleanup()

    def test_direct_replay_lightweight_consistency(self):
        proc = SPQualityProcessor()
        for case in ("normal_high_quality", "weak_signal", "upper_saturation"):
            tmp, session_path, session, samples = record_scenario(case, duration_s=8.0, random_seed=1001)
            try:
                direct = _quality_fingerprint(proc.process(session, samples))
                replay_session, replay_samples = load_replay_samples(session_path)
                replay = _quality_fingerprint(proc.process(replay_session, replay_samples))
                self.assertEqual(direct, replay, case)
            finally:
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
