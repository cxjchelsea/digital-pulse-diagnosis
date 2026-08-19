"""M1-P4A 规则核心行为测试：安全鉴别、质量矩阵、重试 off-by-one。"""

from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import (
    DecisionAction,
    QualityLabel,
    RawPersistenceStatus,
)
from digital_pulse.m1_int import I1PolicyConfig, I1RuleEngine, M1IntError, project_m1_decision

from _m1_p4a_helpers import (
    POLICY_DECIDED_AT_A,
    early_failure_context,
    make_context,
)


def _project(context, evaluation, policy):
    return project_m1_decision(context, evaluation, policy, decided_at_utc=POLICY_DECIDED_AT_A)


class I1RuleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = I1RuleEngine()
        self.policy = I1PolicyConfig()

    def _eval(self, context):
        return self.engine.evaluate(context, self.policy)

    def _action_reasons(self, context):
        evaluation = self._eval(context)
        decision = _project(context, evaluation, self.policy)
        return evaluation.recommended_action, list(evaluation.canonical_reason_codes), decision

    def test_all_six_i1_actions_are_independently_reachable(self) -> None:
        cases = {
            DecisionAction.ACCEPT: make_context(quality_label=QualityLabel.ACCEPTABLE),
            DecisionAction.RETRY_SAME_POSITION: make_context(
                quality_label=QualityLabel.WEAK_SIGNAL, retry_count=0
            ),
            DecisionAction.REPOSITION: make_context(quality_label=QualityLabel.NO_CONTACT),
            DecisionAction.MANUAL_REVIEW: make_context(
                quality_label=QualityLabel.REFERENCE_MISMATCH
            ),
            DecisionAction.STOP: make_context(quality_label=QualityLabel.SATURATED),
            DecisionAction.ABORT_AND_RELEASE: early_failure_context(
                emergency_stop=True, completion_reason="abort_and_release", device_state="SAFE_HOLD"
            ),
        }
        seen = set()
        for expected, context in cases.items():
            action, _reasons, decision = self._action_reasons(context)
            self.assertEqual(action, expected)
            self.assertEqual(decision.action, expected)
            seen.add(action.value)
        self.assertEqual(
            seen,
            {
                "accept",
                "retry_same_position",
                "reposition",
                "manual_review",
                "stop",
                "abort_and_release",
            },
        )

    def test_all_quality_labels_have_explicit_paths(self) -> None:
        expected = {
            QualityLabel.ACCEPTABLE: ("accept", ["quality_acceptable"]),
            QualityLabel.WEAK_SIGNAL: ("retry_same_position", ["weak_signal"]),
            QualityLabel.NO_CONTACT: ("reposition", ["no_contact"]),
            QualityLabel.SATURATED: ("stop", ["saturated"]),
            QualityLabel.UNSTABLE_BASELINE: ("retry_same_position", ["unstable_baseline"]),
            QualityLabel.MOTION_ARTIFACT: ("retry_same_position", ["motion_artifact"]),
            QualityLabel.INSUFFICIENT_DURATION: ("retry_same_position", ["insufficient_duration"]),
            QualityLabel.DATA_INTEGRITY_FAILURE: ("stop", ["data_integrity_failure"]),
            QualityLabel.REFERENCE_MISMATCH: ("manual_review", ["reference_mismatch"]),
            QualityLabel.MANUAL_REVIEW_REQUIRED: ("manual_review", ["manual_review_required"]),
        }
        for label, (action, reasons) in expected.items():
            got_action, got_reasons, _decision = self._action_reasons(
                make_context(quality_label=label, retry_count=0)
            )
            self.assertEqual(got_action.value, action, label)
            self.assertEqual(got_reasons, reasons, label)

    def test_retry_off_by_one_stores_pre_evaluation_count(self) -> None:
        first = make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=0)
        action, reasons, decision = self._action_reasons(first)
        self.assertEqual(action, DecisionAction.RETRY_SAME_POSITION)
        self.assertEqual(reasons, ["weak_signal"])
        self.assertEqual(decision.retry_count, 0)

        second = make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=1)
        action, reasons, decision = self._action_reasons(second)
        self.assertEqual(action, DecisionAction.RETRY_SAME_POSITION)
        self.assertEqual(reasons, ["weak_signal"])
        self.assertEqual(decision.retry_count, 1)

        third = make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=2)
        action, reasons, decision = self._action_reasons(third)
        self.assertEqual(action, DecisionAction.REPOSITION)
        self.assertEqual(reasons, ["weak_signal", "retry_limit_reached"])
        self.assertEqual(decision.retry_count, 2)
        self.assertEqual(decision.max_retry_count, 2)

    def test_retry_exhaustion_matrix_at_count_two(self) -> None:
        labels = (
            QualityLabel.WEAK_SIGNAL,
            QualityLabel.UNSTABLE_BASELINE,
            QualityLabel.MOTION_ARTIFACT,
            QualityLabel.INSUFFICIENT_DURATION,
        )
        for label in labels:
            action, reasons, decision = self._action_reasons(
                make_context(quality_label=label, retry_count=2)
            )
            self.assertEqual(action, DecisionAction.REPOSITION, label)
            self.assertEqual(reasons, [label.value, "retry_limit_reached"], label)
            self.assertEqual(decision.retry_count, 2)

    def test_no_contact_does_not_consume_retry_budget(self) -> None:
        action, reasons, _decision = self._action_reasons(
            make_context(quality_label=QualityLabel.NO_CONTACT, retry_count=0)
        )
        self.assertEqual(action, DecisionAction.REPOSITION)
        self.assertEqual(reasons, ["no_contact"])
        self.assertNotIn("retry_limit_reached", reasons)

    def test_reference_mismatch_ignores_retry_budget(self) -> None:
        action, reasons, _decision = self._action_reasons(
            make_context(quality_label=QualityLabel.REFERENCE_MISMATCH, retry_count=0)
        )
        self.assertEqual(action, DecisionAction.MANUAL_REVIEW)
        self.assertEqual(reasons, ["reference_mismatch"])

    def test_sensor_disconnect_false_abort(self) -> None:
        context = early_failure_context(
            device_state="FAULT",
            completion_reason="device_fault",
            sensor_connection_failure=True,
            emergency_stop=False,
            hard_overload=False,
            host_timeout=False,
            watchdog_timeout=False,
        )
        action, reasons, _decision = self._action_reasons(context)
        self.assertEqual(action, DecisionAction.STOP)
        self.assertEqual(reasons, ["data_integrity_failure"])

    def test_buffer_overflow_device_fault_aborts(self) -> None:
        context = early_failure_context(
            device_state="FAULT",
            completion_reason="complete",
            sensor_connection_failure=False,
            buffer_overflow=True,
        )
        action, reasons, _decision = self._action_reasons(context)
        self.assertEqual(action, DecisionAction.ABORT_AND_RELEASE)
        self.assertEqual(reasons, ["device_fault"])

    def test_unclassified_fault_emits_no_decision(self) -> None:
        context = early_failure_context(device_state="FAULT", completion_reason="complete")
        with self.assertRaises(M1IntError) as raised:
            self._eval(context)
        self.assertEqual(raised.exception.code, "unsupported_device_state")

    def test_unclassified_safe_hold_emits_no_decision(self) -> None:
        context = early_failure_context(device_state="SAFE_HOLD", completion_reason="complete")
        with self.assertRaises(M1IntError) as raised:
            self._eval(context)
        self.assertEqual(raised.exception.code, "unsupported_device_state")

    def test_safe_hold_with_emergency_aborts(self) -> None:
        context = early_failure_context(
            device_state="SAFE_HOLD",
            emergency_stop=True,
            completion_reason="abort_and_release",
        )
        action, reasons, _decision = self._action_reasons(context)
        self.assertEqual(action, DecisionAction.ABORT_AND_RELEASE)
        self.assertEqual(reasons, ["emergency_stop"])

    def test_ordinary_operator_stop_is_stop(self) -> None:
        action, reasons, _decision = self._action_reasons(
            make_context(quality_label=QualityLabel.ACCEPTABLE, operator_stop=True)
        )
        self.assertEqual(action, DecisionAction.STOP)
        self.assertEqual(reasons, ["operator_stop"])

    def test_safety_precedence_adversarial_matrix(self) -> None:
        cases = [
            (
                make_context(quality_label=QualityLabel.ACCEPTABLE, emergency_stop=True),
                "abort_and_release",
                ["emergency_stop"],
            ),
            (
                early_failure_context(
                    quality_label=QualityLabel.ACCEPTABLE,
                    window_id="window-0001",
                    analysis_allowed=True,
                    app_run_id="run-p4a-001",
                    app_analysis_fingerprint="b" * 64,
                    sp_result_fingerprint="c" * 64,
                    run_signal_processing_version="0.4.0-p2d",
                    session_signal_processing_version="0.4.0-p2d",
                    device_state="FAULT",
                    completion_reason="device_fault",
                    device_fault=True,
                ),
                "abort_and_release",
                ["device_fault"],
            ),
            (
                make_context(quality_label=QualityLabel.ACCEPTABLE, operator_stop=True),
                "stop",
                ["operator_stop"],
            ),
            (
                early_failure_context(
                    raw_persistence_status=RawPersistenceStatus.FAILED,
                    completion_reason="complete",
                ),
                "stop",
                ["data_integrity_failure"],
            ),
            (
                make_context(quality_label=QualityLabel.WEAK_SIGNAL, emergency_stop=True),
                "abort_and_release",
                ["emergency_stop"],
            ),
            (
                early_failure_context(
                    quality_label=QualityLabel.WEAK_SIGNAL,
                    window_id="window-0001",
                    analysis_allowed=True,
                    app_run_id="run-p4a-001",
                    app_analysis_fingerprint="b" * 64,
                    sp_result_fingerprint="c" * 64,
                    run_signal_processing_version="0.4.0-p2d",
                    session_signal_processing_version="0.4.0-p2d",
                    device_state="FAULT",
                    completion_reason="device_fault",
                    device_fault=True,
                    retry_count=0,
                ),
                "abort_and_release",
                ["device_fault"],
            ),
            (
                make_context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=2),
                "reposition",
                ["weak_signal", "retry_limit_reached"],
            ),
            (
                make_context(quality_label=QualityLabel.REFERENCE_MISMATCH, retry_count=0),
                "manual_review",
                ["reference_mismatch"],
            ),
            (
                early_failure_context(
                    quality_label=QualityLabel.MANUAL_REVIEW_REQUIRED,
                    window_id="window-0001",
                    analysis_allowed=True,
                    app_run_id="run-p4a-001",
                    app_analysis_fingerprint="b" * 64,
                    sp_result_fingerprint="c" * 64,
                    run_signal_processing_version="0.4.0-p2d",
                    session_signal_processing_version="0.4.0-p2d",
                    device_state="FAULT",
                    completion_reason="device_fault",
                    device_fault=True,
                ),
                "abort_and_release",
                ["device_fault"],
            ),
            (
                make_context(quality_label=QualityLabel.SATURATED, hard_overload=True),
                "abort_and_release",
                ["hard_overload"],
            ),
        ]
        for context, action, reasons in cases:
            got_action, got_reasons, _decision = self._action_reasons(context)
            self.assertEqual(got_action.value, action)
            self.assertEqual(got_reasons, reasons)

    def test_raw_persistence_failure_without_app_run(self) -> None:
        context = early_failure_context(
            raw_persistence_status=RawPersistenceStatus.FAILED,
            completion_reason="complete",
        )
        action, reasons, decision = self._action_reasons(context)
        self.assertEqual(action, DecisionAction.STOP)
        self.assertEqual(reasons, ["data_integrity_failure"])
        self.assertIsNone(decision.quality_reference)
        self.assertIsNone(context.provenance.app_run_id)

    def test_partial_and_not_started_raw_status_fail_closed(self) -> None:
        for status in (RawPersistenceStatus.PARTIAL, RawPersistenceStatus.NOT_STARTED):
            context = early_failure_context(
                raw_persistence_status=status, completion_reason="complete"
            )
            with self.assertRaises(M1IntError) as raised:
                self._eval(context)
            self.assertEqual(raised.exception.code, "invalid_input", status)

    def test_frame_loss_and_timestamp_regression_stop(self) -> None:
        action, reasons, _decision = self._action_reasons(make_context(frame_loss=True))
        self.assertEqual(action, DecisionAction.STOP)
        self.assertEqual(reasons, ["data_integrity_failure"])
        action, reasons, _decision = self._action_reasons(make_context(timestamp_regression=True))
        self.assertEqual(action, DecisionAction.STOP)
        self.assertEqual(reasons, ["data_integrity_failure"])

    def test_acceptable_with_analysis_not_allowed_fail_closed(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self._eval(make_context(quality_label=QualityLabel.ACCEPTABLE, analysis_allowed=False))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_never_returns_reserved_actions(self) -> None:
        contexts = [
            make_context(quality_label=label)
            for label in QualityLabel
        ] + [
            early_failure_context(emergency_stop=True, completion_reason="abort_and_release"),
            early_failure_context(
                raw_persistence_status=RawPersistenceStatus.FAILED, completion_reason="complete"
            ),
        ]
        reserved = {"hold", "adjust_pressure", "continue_scan"}
        for context in contexts:
            evaluation = self._eval(context)
            self.assertNotIn(evaluation.recommended_action.value, reserved)


if __name__ == "__main__":
    unittest.main()
