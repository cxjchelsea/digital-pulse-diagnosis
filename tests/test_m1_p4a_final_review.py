"""M1-P4A Final Review 回归：仅覆盖本轮发现的失败关闭漏洞。"""

from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import DecisionAction, ParameterStatus, QualityLabel, RawPersistenceStatus
from digital_pulse.m1_int import (
    DecisionEvaluation,
    I1PolicyConfig,
    I1RuleEngine,
    M1IntError,
    RULE_VERSION,
    policy_configuration_digest,
    project_m1_decision,
)
from digital_pulse.m1_p4a_acceptance import run_m1_p4a_acceptance

from _m1_p4a_helpers import (
    POLICY_DECIDED_AT_A,
    early_failure_context,
    make_context,
)


class FinalReviewFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = I1RuleEngine()
        self.policy = I1PolicyConfig()

    def _eval(self, context):
        return self.engine.evaluate(context, self.policy)

    def test_cross_context_projection_is_rejected(self) -> None:
        evaluation = self._eval(make_context())
        emergency = early_failure_context(
            emergency_stop=True,
            completion_reason="abort_and_release",
            device_state="SAFE_HOLD",
        )
        with self.assertRaises(M1IntError) as raised:
            project_m1_decision(
                emergency, evaluation, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
            )
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_forged_accept_evaluation_cannot_override_emergency(self) -> None:
        emergency = early_failure_context(
            emergency_stop=True,
            completion_reason="abort_and_release",
            device_state="SAFE_HOLD",
        )
        forged = DecisionEvaluation(
            recommended_action=DecisionAction.ACCEPT,
            canonical_reason_codes=("quality_acceptable",),
            matched_rule_id="forged.accept",
            rule_priority=6,
            semantic_input_digest="0" * 64,
            rule_version=RULE_VERSION,
            configuration_digest=policy_configuration_digest(self.policy),
            evidence_refs=("session:x",),
            human_readable_explanation="forged",
            history_fingerprint="0" * 64,
        )
        with self.assertRaises(M1IntError) as raised:
            project_m1_decision(
                emergency, forged, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
            )
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_acceptable_requires_analysis_allowed_true(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(analysis_allowed=None))
        self.assertEqual(raised.exception.code, "invalid_input")
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(analysis_allowed=False))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_retry_history_must_match_retry_count(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self._eval(
                make_context(
                    quality_label=QualityLabel.WEAK_SIGNAL,
                    retry_count=2,
                    prior_actions=(),
                    prior_decision_ids=(),
                )
            )
        self.assertEqual(raised.exception.code, "invalid_retry_state")
        with self.assertRaises(M1IntError) as raised:
            self._eval(
                make_context(
                    quality_label=QualityLabel.WEAK_SIGNAL,
                    retry_count=1,
                    prior_actions=("accept",),
                    prior_decision_ids=("m1-decision-" + "a" * 64,),
                )
            )
        self.assertEqual(raised.exception.code, "invalid_retry_state")
        with self.assertRaises(M1IntError) as raised:
            self._eval(
                make_context(
                    quality_label=QualityLabel.WEAK_SIGNAL,
                    retry_count=0,
                    prior_actions=("banana",),
                    prior_decision_ids=("m1-decision-" + "a" * 64,),
                )
            )
        self.assertEqual(raised.exception.code, "invalid_retry_state")

    def test_device_fault_and_buffer_overflow_require_fault_state(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(device_fault=True, device_state="ACQUIRE"))
        self.assertEqual(raised.exception.code, "invalid_input")
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(buffer_overflow=True, device_state="ACQUIRE"))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_unknown_device_state_fails_in_engine(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(device_state="BANANA"))
        self.assertEqual(raised.exception.code, "unsupported_device_state")

    def test_sensor_disconnect_wins_over_device_fault_bool(self) -> None:
        evaluation = self._eval(
            early_failure_context(
                device_state="FAULT",
                completion_reason="device_fault",
                sensor_connection_failure=True,
                device_fault=True,
            )
        )
        self.assertEqual(evaluation.recommended_action, DecisionAction.STOP)
        self.assertEqual(list(evaluation.canonical_reason_codes), ["data_integrity_failure"])

    def test_parameter_status_and_sp_version_change_decision_id(self) -> None:
        pending = make_context(parameter_status=ParameterStatus.PENDING_H1_CALIBRATION)
        synthetic = make_context(parameter_status=ParameterStatus.SYNTHETIC_ONLY)
        first = project_m1_decision(
            pending, self._eval(pending), self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        second = project_m1_decision(
            synthetic, self._eval(synthetic), self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        self.assertNotEqual(first.decision_id, second.decision_id)
        self.assertNotEqual(first.to_json(), second.to_json())

        failed_x = early_failure_context(
            raw_persistence_status=RawPersistenceStatus.FAILED,
            session_signal_processing_version="0.4.0-p2d",
        )
        failed_y = early_failure_context(
            raw_persistence_status=RawPersistenceStatus.FAILED,
            session_signal_processing_version="9.9.9",
        )
        id_x = project_m1_decision(
            failed_x, self._eval(failed_x), self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        id_y = project_m1_decision(
            failed_y, self._eval(failed_y), self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        self.assertNotEqual(id_x.decision_id, id_y.decision_id)

    def test_non_frozen_policy_is_rejected_by_projection(self) -> None:
        context = make_context()
        evaluation = self._eval(context)
        other = I1PolicyConfig(priority_table_version="i1-priority-v-forged")
        with self.assertRaises(M1IntError) as raised:
            project_m1_decision(context, evaluation, other, decided_at_utc=POLICY_DECIDED_AT_A)
        self.assertEqual(raised.exception.code, "version_mismatch")

    def test_acceptance_top_level_fields_derive_from_gates(self) -> None:
        import subprocess

        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        result = run_m1_p4a_acceptance(software_commit_sha=head, expected_head_sha=head)
        for name, payload in result["gates"].items():
            if isinstance(result.get(name), bool):
                self.assertEqual(result[name], payload["passed"], name)
        self.assertEqual(result["acceptance"], result["failed_gates"] == [])
        self.assertTrue(result["acceptance"])
        self.assertEqual(result["failed_gates"], [])


if __name__ == "__main__":
    unittest.main()
