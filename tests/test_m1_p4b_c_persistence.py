"""P4B-C event / override / outcome persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest

from digital_pulse.m1_contracts import (
    DecisionAction,
    DecisionInputVersions,
    I1_ACTIONS,
    M1Decision,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import (
    ALREADY_COMMITTED,
    COMMITTED,
    DecisionLedger,
    DecisionSourceProvenance,
    M1IntError,
    OverrideClassification,
)
from digital_pulse.m1_int.override_safety import ALLOWED_OVERRIDE_TARGETS

SESSION = "session-p4b-c-a"
DECISION = "m1-decision-" + ("aa" * 32)
SOFTWARE_A = "a" * 40
SOFTWARE_B = "b" * 40
CONFIG = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
SCOPE = "m1-retry-scope-" + ("ab" * 32)


def _decision(action: DecisionAction = DecisionAction.ACCEPT) -> M1Decision:
    return M1Decision(
        decision_id=DECISION,
        session_id=SESSION,
        decided_at_utc=CLOCK,
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=SESSION, window_id="window-0001"),
        action=action,
        reason_codes=("emergency_stop",) if action is DecisionAction.ABORT_AND_RELEASE else ("quality_acceptable",),
        rule_version="i1-pre-0.1.0",
        input_versions=DecisionInputVersions(
            signal_processing_version="0.4.0-p2d",
            decision_rule_version="i1-pre-0.1.0",
            configuration_digest=CONFIG,
        ),
        retry_count=0,
        max_retry_count=2,
        operator_override=None,
        outcome=None,
        parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
    )


def _provenance(software: str = SOFTWARE_A) -> DecisionSourceProvenance:
    return DecisionSourceProvenance(
        app_run_id="run-p4b-c",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=software,
    )


def _ledger(root: Path, injector=None) -> DecisionLedger:
    return DecisionLedger(root, clock=lambda: CLOCK, failure_injector=injector)


def _fail_at(point: str):
    def injector(actual: str) -> None:
        if actual == point:
            raise M1IntError("persistence_failure", f"injected failure at {actual}")

    return injector


class EventPersistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "_p4bc_tmp" / self._testMethodName
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, action: DecisionAction = DecisionAction.ACCEPT) -> DecisionLedger:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(action), _provenance())
        return ledger

    def test_override_allow_and_seq(self) -> None:
        ledger = self._seed()
        result = ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="stop",
            operator_id="op-001",
            note="stop now",
            source_provenance=_provenance(),
        )
        self.assertEqual(result.status, COMMITTED)
        self.assertEqual(result.event_type, "operator_override")
        self.assertEqual(result.event_seq, 2)
        self.assertEqual(result.classification, OverrideClassification.ALLOWED)
        loaded = ledger.load_machine_decision(SESSION, DECISION)
        self.assertIsNone(loaded.operator_override)
        self.assertIsNone(loaded.outcome)
        events = (self.root / SESSION / "int" / "decision-events.jsonl").read_text(encoding="utf-8")
        self.assertIn("operator_override", events)
        manifest = ledger.verify_decision_ledger_minimal(SESSION)
        self.assertEqual(manifest.event_count, 2)
        self.assertEqual(manifest.last_event_seq, 2)
        self.assertEqual(manifest.decision_count, 1)

    def test_override_deny_writes_safety_rejection(self) -> None:
        ledger = self._seed()
        result = ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="accept",
            operator_id="op-001",
            note="same then weaken",
            source_provenance=_provenance(),
        )
        self.assertEqual(result.classification, OverrideClassification.IDEMPOTENT_SAME_ACTION)
        denied = ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="retry_same_position",
            operator_id="op-001",
            note="illegal weaken",
            source_provenance=_provenance(),
        )
        self.assertEqual(denied.event_type, "action_rejected_by_safety")
        self.assertEqual(denied.classification, OverrideClassification.REJECTED_BY_SAFETY)
        loaded = ledger.load_machine_decision(SESSION, DECISION)
        self.assertEqual(loaded.action, DecisionAction.ACCEPT)
        self.assertIsNone(loaded.operator_override)

    def test_same_action_is_not_ledger_noop(self) -> None:
        ledger = self._seed(DecisionAction.STOP)
        first = ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="stop",
            operator_id="op-001",
            note="confirm stop",
            source_provenance=_provenance(),
        )
        self.assertEqual(first.status, COMMITTED)
        self.assertEqual(first.classification, OverrideClassification.IDEMPOTENT_SAME_ACTION)
        retry = ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="stop",
            operator_id="op-001",
            note="confirm stop",
            source_provenance=_provenance(),
        )
        self.assertEqual(retry.status, ALREADY_COMMITTED)
        self.assertEqual(retry.event_id, first.event_id)

    def test_duplicate_conflict_and_provenance(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="stop",
            operator_id="op-001",
            note="first",
            source_provenance=_provenance(),
        )
        with self.assertRaises(M1IntError) as conflict:
            ledger.persist_operator_override(
                SESSION,
                DECISION,
                requested_action="manual_review",
                operator_id="op-001",
                note="second",
                source_provenance=_provenance(),
            )
        self.assertEqual(conflict.exception.code, "duplicate_conflict")
        with self.assertRaises(M1IntError) as provenance:
            ledger.persist_operator_override(
                SESSION,
                DECISION,
                requested_action="stop",
                operator_id="op-001",
                note="first",
                source_provenance=_provenance(SOFTWARE_B),
            )
        self.assertEqual(provenance.exception.code, "provenance_mismatch")

    def test_supplied_event_id_conflict(self) -> None:
        ledger = self._seed()
        first = ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        with self.assertRaises(M1IntError) as exc:
            ledger.persist_decision_completed(
                SESSION,
                DECISION,
                source_provenance=_provenance(),
                event_id=first.event_id,
            )
        self.assertEqual(exc.exception.code, "duplicate_conflict")

    def test_outcome_and_manual_review_resolution(self) -> None:
        ledger = self._seed(DecisionAction.MANUAL_REVIEW)
        applied = ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        completed = ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        resolved = ledger.persist_manual_review_resolution(
            SESSION,
            DECISION,
            resolution="remain_awaiting",
            operator_id="op-001",
            source_provenance=_provenance(),
        )
        self.assertEqual(applied.event_type, "action_applied")
        self.assertEqual(completed.event_type, "decision_completed")
        self.assertEqual(resolved.event_type, "manual_review_resolved")
        self.assertFalse((self.root / SESSION / "app").exists())
        machine = ledger.load_machine_decision(SESSION, DECISION)
        self.assertIsNone(machine.outcome)

    def test_illegal_outcome_and_missing_operator(self) -> None:
        ledger = self._seed()
        with self.assertRaises(M1IntError) as outcome:
            ledger.persist_manual_review_resolution(
                SESSION,
                DECISION,
                resolution="launch_retry_scope",
                operator_id="op-001",
                source_provenance=_provenance(),
            )
        self.assertEqual(outcome.exception.code, "invalid_input")
        with self.assertRaises(M1IntError) as operator:
            ledger.persist_operator_override(
                SESSION,
                DECISION,
                requested_action="stop",
                operator_id="",
                note="missing",
                source_provenance=_provenance(),
            )
        self.assertEqual(operator.exception.code, "invalid_input")

    def test_p4c_facts_do_not_change_retry_count(self) -> None:
        ledger = self._seed()
        started = ledger.persist_retry_scope_started(
            SESSION,
            retry_scope_id=SCOPE,
            source_provenance=_provenance(),
        )
        linked = ledger.persist_retry_attempt_linked(
            SESSION,
            DECISION,
            retry_scope_id=SCOPE,
            linked_session_id="session-next",
            source_provenance=_provenance(),
        )
        closed = ledger.persist_retry_scope_closed(
            SESSION,
            retry_scope_id=SCOPE,
            source_provenance=_provenance(),
        )
        self.assertEqual(started.event_type, "retry_scope_started")
        self.assertEqual(linked.event_type, "retry_attempt_linked")
        self.assertEqual(closed.event_type, "retry_scope_closed")
        machine = ledger.load_machine_decision(SESSION, DECISION)
        self.assertEqual(machine.retry_count, 0)

    def test_machine_bytes_immutable(self) -> None:
        ledger = self._seed()
        before = (self.root / SESSION / "int" / "decisions.jsonl").read_bytes()
        ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="stop",
            operator_id="op-001",
            note="stop",
            source_provenance=_provenance(),
        )
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        after = (self.root / SESSION / "int" / "decisions.jsonl").read_bytes()
        self.assertEqual(before, after)

    def test_concurrency_unique_seq(self) -> None:
        ledger = self._seed()

        def write(note: str):
            return ledger.persist_operator_override(
                SESSION,
                DECISION,
                requested_action="stop",
                operator_id="op-001",
                note=note,
                source_provenance=_provenance(),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(write, "first")
            second = pool.submit(write, "first")
            results = [first.result(), second.result()]
        statuses = {item.status for item in results}
        self.assertEqual(statuses, {COMMITTED, ALREADY_COMMITTED})
        events = [
            json.loads(line)
            for line in (self.root / SESSION / "int" / "decision-events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        seqs = [item["event_seq"] for item in events]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))

    def test_crash_recovery_event_only(self) -> None:
        seed = self._seed()
        del seed
        for point in ("pending_write", "events_append", "manifest_write", "pending_delete"):
            crash_root = self.root / point
            crash_root.mkdir()
            DecisionLedger(crash_root, clock=lambda: CLOCK).append_decision(_decision(), _provenance())
            try:
                DecisionLedger(crash_root, clock=lambda: CLOCK, failure_injector=_fail_at(point)).persist_action_applied(
                    SESSION,
                    DECISION,
                    source_provenance=_provenance(),
                )
                self.fail(point)
            except M1IntError as exc:
                self.assertEqual(exc.code, "persistence_failure")
            recovered = DecisionLedger(crash_root, clock=lambda: CLOCK)
            recovered.recover_pending_commit(SESSION)
            manifest = recovered.verify_decision_ledger_minimal(SESSION)
            if point == "pending_write":
                self.assertEqual(manifest.event_count, 1)
            else:
                self.assertGreaterEqual(manifest.event_count, 1)

    def test_unbound_partial_tail_fail_closed(self) -> None:
        ledger = self._seed()
        events = self.root / SESSION / "int" / "decision-events.jsonl"
        events.write_bytes(events.read_bytes() + b'{"event_type":"partial"')
        with self.assertRaises(M1IntError) as exc:
            ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        self.assertEqual(exc.exception.code, "ledger_untrusted")

    def test_override_matrix_pairs(self) -> None:
        for machine in I1_ACTIONS:
            for requested in I1_ACTIONS:
                root = self.root / f"{machine}-{requested}"
                root.mkdir(parents=True)
                ledger = _ledger(root)
                ledger.append_decision(_decision(DecisionAction(machine)), _provenance())
                result = ledger.persist_operator_override(
                    SESSION,
                    DECISION,
                    requested_action=requested,
                    operator_id="op-001",
                    note=f"{machine}->{requested}",
                    source_provenance=_provenance(),
                )
                if machine == requested:
                    self.assertEqual(result.classification, OverrideClassification.IDEMPOTENT_SAME_ACTION)
                    self.assertEqual(result.event_type, "operator_override")
                elif requested in ALLOWED_OVERRIDE_TARGETS[machine]:
                    self.assertEqual(result.classification, OverrideClassification.ALLOWED)
                    self.assertEqual(result.event_type, "operator_override")
                else:
                    self.assertEqual(result.classification, OverrideClassification.REJECTED_BY_SAFETY)
                    self.assertEqual(result.event_type, "action_rejected_by_safety")


if __name__ == "__main__":
    unittest.main()
