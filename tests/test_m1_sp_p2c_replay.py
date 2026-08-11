from __future__ import annotations

import unittest

from digital_pulse.m1_sp.processor import create_p2c_processor

from _m1_sp_helpers import load_replay_samples, record_scenario


def _beat_fingerprint(result):
    q = result.quality_results[0]
    analysis = result.beats_by_window[q.window_id]
    ref = result.reference_by_window[q.window_id]
    return {
        "label": q.label.value,
        "reason_codes": list(q.reason_codes),
        "beat_count": q.metrics.get("beat_count"),
        "ppg_match_rate": q.metrics.get("ppg_match_rate"),
        "peak_indices": [c.peak_index for c in analysis.candidates if c.valid],
        "peak_times": [c.peak_device_time_us for c in analysis.candidates if c.valid],
        "beat_ids": [s.beat_id for s in analysis.segments],
        "matched_count": ref.matched_count,
        "median_lag_ms": ref.median_lag_ms,
    }


class M1SPP2CReplayTests(unittest.TestCase):
    def test_direct_equals_replay_for_normal_and_weak(self):
        proc = create_p2c_processor()
        for case in ("normal_high_quality", "weak_signal"):
            tmp, session_path, session, samples = record_scenario(case, duration_s=8.0, random_seed=1001)
            try:
                direct = _beat_fingerprint(proc.process(session, samples))
                replay_session, replay_samples = load_replay_samples(session_path)
                replay = _beat_fingerprint(proc.process(replay_session, replay_samples))
                self.assertEqual(direct, replay, case)
            finally:
                tmp.cleanup()

    def test_ppg_misalignment_direct_replay(self):
        proc = create_p2c_processor()
        tmp, session_path, session, samples = record_scenario(
            "ppg_misalignment", duration_s=8.0, random_seed=1001
        )
        try:
            direct = _beat_fingerprint(proc.process(session, samples))
            replay_session, replay_samples = load_replay_samples(session_path)
            replay = _beat_fingerprint(proc.process(replay_session, replay_samples))
            self.assertEqual(direct, replay)
            self.assertEqual(direct["label"], "reference_mismatch")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
