from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from digital_pulse.m1_sp.processor import SPPreprocessor

from _m1_sp_helpers import record_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SP_ROOT = REPO_ROOT / "src" / "digital_pulse" / "m1_sp"


class M1SPOracleIsolationTests(unittest.TestCase):
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
                    self.assertFalse(any(module == item or module.startswith(item + ".") for item in forbidden_imports), path.name)
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, path.name)
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_names, path.name)

    def test_delete_scenario_and_expected_does_not_change_result(self):
        tmp, session_path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=51)
        try:
            before = SPPreprocessor().preprocess(session, samples)
            (session_path / "scenario.json").unlink()
            (session_path / "expected.json").unlink()
            after = SPPreprocessor().preprocess(session, samples)
            self.assertEqual(before.parameter_digest, after.parameter_digest)
            self.assertEqual(before.integrity.integrity_ok, after.integrity.integrity_ok)
            self.assertEqual(before.windows.selected_window_id, after.windows.selected_window_id)
            self.assertEqual(
                list(before.normalized.frame_sequence),
                list(after.normalized.frame_sequence),
            )
        finally:
            tmp.cleanup()

    def test_tampered_expected_does_not_change_result(self):
        tmp, session_path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=52)
        try:
            before = SPPreprocessor().preprocess(session, samples)
            expected_path = session_path / "expected.json"
            payload = json.loads(expected_path.read_text(encoding="utf-8"))
            if "expected_quality_label" in payload:
                payload["expected_quality_label"] = "motion_artifact"
            elif "quality_label" in payload:
                payload["quality_label"] = "motion_artifact"
            else:
                payload["expected_quality_label"] = "motion_artifact"
            expected_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            after = SPPreprocessor().preprocess(session, samples)
            self.assertEqual(before.integrity.missing_frame_count, after.integrity.missing_frame_count)
            self.assertEqual(before.windows.selected_window_id, after.windows.selected_window_id)
            self.assertEqual(
                [e.code for e in before.integrity.evidence],
                [e.code for e in after.integrity.evidence],
            )
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
