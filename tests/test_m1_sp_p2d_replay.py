from __future__ import annotations

import unittest

from digital_pulse.m1_sp import SPProcessingProvenance, SPProcessor, compare_sp_results

from _m1_sp_helpers import FIXED_SHA, load_replay_samples, record_scenario


class M1SPP2DReplayTests(unittest.TestCase):
    def test_formal_result_matches_replay_for_representative_cases(self):
        for case in ("normal_high_quality", "ppg_misalignment", "raw_persistence_failure"):
            tmp, path, session, samples = record_scenario(case, duration_s=8.0, random_seed=1001)
            try:
                provenance = SPProcessingProvenance(FIXED_SHA)
                direct = SPProcessor().process(session, samples, provenance=provenance)
                replay_session, replay_samples = load_replay_samples(
                    path, allow_incomplete=not session.completed
                )
                replayed = SPProcessor().process(
                    replay_session, replay_samples, provenance=provenance
                )
                self.assertTrue(compare_sp_results(direct, replayed), case)
            finally:
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
