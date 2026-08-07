from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from digital_pulse.m1_sp.processor import create_p2c_processor

from _m1_sp_helpers import load_replay_samples, record_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SP_ROOT = REPO_ROOT / "src" / "digital_pulse" / "m1_sp"


def _p2c_fingerprint(result):
    q = result.quality_results[0] if result.quality_results else None
    beats = None
    if q is not None and q.window_id in result.beats_by_window:
        analysis = result.beats_by_window[q.window_id]
        beats = {
            "beat_count": analysis.beat_count,
            "peak_indices": [c.peak_index for c in analysis.candidates if c.valid],
            "peak_times": [c.peak_device_time_us for c in analysis.candidates if c.valid],
            "beat_ids": [s.beat_id for s in analysis.segments],
        }
    ref = None
    if q is not None and q.window_id in result.reference_by_window:
        summary = result.reference_by_window[q.window_id]
        ref = {
            "match_rate": summary.match_rate,
            "matched_count": summary.matched_count,
            "median_lag_ms": summary.median_lag_ms,
        }
    return {
        "processing_status": result.processing_status,
        "parameter_version": result.parameter_version,
        "processing_version": result.processing_version,
        "configuration_digest": result.configuration_digest,
        "labels": [item.label.value for item in result.quality_results],
        "reason_codes": [list(item.reason_codes) for item in result.quality_results],
        "metrics": [dict(item.metrics) for item in result.quality_results],
        "beats": beats,
        "reference": ref,
    }


class M1SPP2COracleIsolationTests(unittest.TestCase):
    def test_production_forbids_simulator_oracle_symbols(self):
        forbidden_imports = {
            "digital_pulse.m1_simulator.scenarios",
            "digital_pulse.m1_simulator.faults",
            "digital_pulse.m1_simulator.device_faults",
            "digital_pulse.m1_simulator.transport",
        }
        forbidden_names = {
            "ScenarioDefinition",
            "ScenarioConfig",
            "expected_quality_label",
            "expected_int_action",
            "FaultKind",
            "BeatTimeline",
            "heart_rate_bpm",
            "ppg_delay",
            "ppg_delay_ms",
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

    def test_delete_scenario_expected_unchanged_for_normal_and_misalignment(self):
        proc = create_p2c_processor()
        for case in ("normal_high_quality", "ppg_misalignment"):
            tmp, session_path, session, samples = record_scenario(case, duration_s=8.0, random_seed=1001)
            try:
                before = _p2c_fingerprint(proc.process(session, samples))
                (session_path / "scenario.json").unlink()
                (session_path / "expected.json").unlink()
                after = _p2c_fingerprint(proc.process(session, samples))
                self.assertEqual(before, after, case)
            finally:
                tmp.cleanup()

    def test_tampered_expected_unchanged(self):
        proc = create_p2c_processor()
        tmp, session_path, session, samples = record_scenario(
            "ppg_misalignment", duration_s=8.0, random_seed=1001
        )
        try:
            before = _p2c_fingerprint(proc.process(session, samples))
            expected_path = session_path / "expected.json"
            payload = json.loads(expected_path.read_text(encoding="utf-8"))
            payload["expected_quality_label"] = "acceptable"
            expected_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            after = _p2c_fingerprint(proc.process(session, samples))
            self.assertEqual(before, after)
            self.assertEqual(after["labels"], ["reference_mismatch"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
