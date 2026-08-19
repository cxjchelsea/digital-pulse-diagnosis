"""M1-P4A 投影、决策身份、确定性与时钟无关性测试。"""

from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import DecisionAction, QualityLabel
from digital_pulse.m1_int import (
    I1PolicyConfig,
    I1RuleEngine,
    RULE_VERSION,
    history_fingerprint,
    policy_configuration_digest,
    project_m1_decision,
)

from _m1_p4a_helpers import (
    POLICY_DECIDED_AT_A,
    POLICY_DECIDED_AT_B,
    SOFTWARE_SHA,
    SP_VERSION,
    make_context,
)


class ProjectionAndIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = I1RuleEngine()
        self.policy = I1PolicyConfig()

    def test_fixed_projection_fields_and_schema(self) -> None:
        context = make_context()
        evaluation = self.engine.evaluate(context, self.policy)
        decision = project_m1_decision(
            context, evaluation, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        decision.validate()
        decision.validate_schema()
        self.assertEqual(decision.milestone, "M1")
        self.assertEqual(decision.int_level, "I1")
        self.assertEqual(decision.schema_version, "1.0.0")
        self.assertEqual(decision.rule_version, RULE_VERSION)
        self.assertEqual(decision.input_versions.decision_rule_version, RULE_VERSION)
        self.assertEqual(decision.input_versions.signal_processing_version, SP_VERSION)
        self.assertEqual(decision.input_versions.configuration_digest, policy_configuration_digest(self.policy))
        self.assertEqual(len(decision.input_versions.configuration_digest or ""), 64)
        self.assertIsNone(decision.operator_override)
        self.assertIsNone(decision.outcome)
        self.assertEqual(decision.retry_count, 0)
        self.assertEqual(decision.max_retry_count, 2)
        self.assertEqual(decision.parameter_status.value, "pending_h1_calibration")
        self.assertTrue(decision.decision_id.startswith("m1-decision-"))
        self.assertEqual(len(decision.decision_id), len("m1-decision-") + 64)
        self.assertNotIn(SOFTWARE_SHA, decision.decision_id)

    def test_accept_does_not_promote_parameter_status(self) -> None:
        context = make_context()
        evaluation = self.engine.evaluate(context, self.policy)
        decision = project_m1_decision(
            context, evaluation, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        self.assertEqual(decision.action, DecisionAction.ACCEPT)
        self.assertEqual(decision.parameter_status.value, "pending_h1_calibration")

    def test_clock_independence_same_decision_id(self) -> None:
        context = make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=1)
        evaluation = self.engine.evaluate(context, self.policy)
        first = project_m1_decision(
            context, evaluation, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        )
        second = project_m1_decision(
            context, evaluation, self.policy, decided_at_utc=POLICY_DECIDED_AT_B
        )
        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(first.action, second.action)
        self.assertEqual(first.reason_codes, second.reason_codes)
        self.assertEqual(first.decided_at_utc, POLICY_DECIDED_AT_A)
        self.assertEqual(second.decided_at_utc, POLICY_DECIDED_AT_B)

    def test_repeated_evaluation_is_deterministic(self) -> None:
        context = make_context(quality_label=QualityLabel.MOTION_ARTIFACT, retry_count=2)
        first = self.engine.evaluate(context, self.policy)
        second = self.engine.evaluate(context, self.policy)
        self.assertEqual(first.recommended_action, second.recommended_action)
        self.assertEqual(first.canonical_reason_codes, second.canonical_reason_codes)
        self.assertEqual(first.matched_rule_id, second.matched_rule_id)
        self.assertEqual(first.semantic_input_digest, second.semantic_input_digest)
        self.assertEqual(first.configuration_digest, second.configuration_digest)
        self.assertEqual(first.human_readable_explanation, second.human_readable_explanation)
        digest_a = history_fingerprint(context.history)
        digest_b = history_fingerprint(context.history)
        self.assertEqual(digest_a, digest_b)
        first_id = project_m1_decision(
            context, first, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        ).decision_id
        second_id = project_m1_decision(
            context, second, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        ).decision_id
        self.assertEqual(first_id, second_id)

    def test_policy_digest_stable_and_changes_with_semantic_fields(self) -> None:
        same_a = I1PolicyConfig()
        same_b = I1PolicyConfig(
            policy_schema_version="i1-policy-v1",
            max_retry_count=2,
            priority_table_version="i1-priority-v1",
            retry_exhaustion_policy_version="i1-retry-exhaustion-v1",
        )
        self.assertEqual(policy_configuration_digest(same_a), policy_configuration_digest(same_b))
        other = I1PolicyConfig(
            policy_schema_version="i1-policy-v1",
            max_retry_count=3,
            priority_table_version="i1-priority-v1",
            retry_exhaustion_policy_version="i1-retry-exhaustion-v1",
        )
        self.assertNotEqual(policy_configuration_digest(same_a), policy_configuration_digest(other))
        self.assertEqual(len(policy_configuration_digest(same_a)), 64)
        self.assertNotEqual(policy_configuration_digest(same_a), "0" * 64)

    def test_different_outputs_do_not_share_decision_id(self) -> None:
        accept_ctx = make_context(quality_label=QualityLabel.ACCEPTABLE)
        retry_ctx = make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=0)
        accept_eval = self.engine.evaluate(accept_ctx, self.policy)
        retry_eval = self.engine.evaluate(retry_ctx, self.policy)
        accept_id = project_m1_decision(
            accept_ctx, accept_eval, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        ).decision_id
        retry_id = project_m1_decision(
            retry_ctx, retry_eval, self.policy, decided_at_utc=POLICY_DECIDED_AT_A
        ).decision_id
        self.assertNotEqual(accept_id, retry_id)
        self.assertNotIn(accept_id, accept_eval.human_readable_explanation)

    def test_distinct_app_runs_do_not_share_identity(self) -> None:
        first = make_context(app_run_id="run-aaa", app_analysis_fingerprint="1" * 64)
        second = make_context(app_run_id="run-bbb", app_analysis_fingerprint="2" * 64)
        first_id = project_m1_decision(
            first,
            self.engine.evaluate(first, self.policy),
            self.policy,
            decided_at_utc=POLICY_DECIDED_AT_A,
        ).decision_id
        second_id = project_m1_decision(
            second,
            self.engine.evaluate(second, self.policy),
            self.policy,
            decided_at_utc=POLICY_DECIDED_AT_A,
        ).decision_id
        self.assertNotEqual(first_id, second_id)

    def test_engine_rejects_non_frozen_max_retry_policy(self) -> None:
        context = make_context()
        bad_policy = I1PolicyConfig(max_retry_count=3)
        with self.assertRaises(Exception) as raised:
            self.engine.evaluate(context, bad_policy)
        self.assertEqual(raised.exception.code, "version_mismatch")


if __name__ == "__main__":
    unittest.main()
