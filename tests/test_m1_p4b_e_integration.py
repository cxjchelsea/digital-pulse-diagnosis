"""P4B-E production-path integration probes. No new business semantics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from digital_pulse.m1_contracts import DecisionAction
from digital_pulse.m1_int import DecisionLedger, M1IntError
from digital_pulse.m1_int.replay import fold_ledger_snapshot
from digital_pulse.m1_int.replay_models import LedgerSnapshot
from digital_pulse.m1_p4b_e_acceptance import (
    CLOCK,
    DECISION_ID,
    SESSION_ID,
    SOFTWARE_SHA,
    _machine,
    _provenance,
)


class P4BEIntegrationTests(unittest.TestCase):
    def test_aggregate_production_path_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp, clock=lambda: CLOCK)
            ledger.append_decision(_machine(), _provenance())
            before = Path(tmp, SESSION_ID, "int", "decisions.jsonl").read_bytes()
            ledger.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="stop",
                operator_id="op-001",
                note="stop now",
                source_provenance=_provenance(),
            )
            ledger.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
            ledger.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
            first = ledger.replay_session(SESSION_ID)
            second = ledger.replay_session(SESSION_ID)
            machine = ledger.load_machine_decision(SESSION_ID, DECISION_ID)
            self.assertEqual(Path(tmp, SESSION_ID, "int", "decisions.jsonl").read_bytes(), before)
            self.assertIsNone(machine.operator_override)
            self.assertIsNone(machine.outcome)
            self.assertEqual(machine.action, DecisionAction.ACCEPT)
            self.assertEqual(first.views[0].machine_action, "accept")
            self.assertEqual(first.views[0].replayed_action, "stop")
            self.assertEqual(first.views[0].outcome, "completed")
            self.assertTrue(first.views[0].completed)
            self.assertEqual(first.views[0].derived_action_at_apply, "stop")
            self.assertEqual(first.views[0].provenance["software_commit_sha"], SOFTWARE_SHA)
            self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)
            self.assertEqual([item.event_seq for item in first.events], list(range(1, len(first.events) + 1)))

    def test_rejected_override_does_not_become_effective(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp, clock=lambda: CLOCK)
            ledger.append_decision(_machine(), _provenance())
            ledger.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="retry_same_position",
                operator_id="op-001",
                note="weaken",
                source_provenance=_provenance(),
            )
            result = ledger.replay_session(SESSION_ID)
            self.assertEqual(result.views[0].replayed_action, "accept")
            self.assertEqual(result.views[0].rejection_facts[0].event_type, "action_rejected_by_safety")

    def test_corrupt_jsonl_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp, clock=lambda: CLOCK)
            ledger.append_decision(_machine(), _provenance())
            Path(tmp, SESSION_ID, "int", "decisions.jsonl").write_text("not-json\n", encoding="utf-8")
            with self.assertRaises(M1IntError) as caught:
                DecisionLedger(tmp, clock=lambda: CLOCK).replay_session(SESSION_ID)
            self.assertEqual(caught.exception.code, "ledger_untrusted")

    def test_unverified_snapshot_cannot_fold(self) -> None:
        with self.assertRaises(M1IntError) as caught:
            fold_ledger_snapshot(
                LedgerSnapshot(
                    session_id=SESSION_ID,
                    machine_decisions=(),
                    events=(),
                    ledger_schema_version="i1-ledger-1.0.0-pre",
                    manifest_schema_version="i1-ledger-manifest-1.0.0-pre",
                    decisions_sha256="ab" * 32,
                    events_sha256="cd" * 32,
                    last_event_seq=0,
                    _token=object(),
                )
            )
        self.assertEqual(caught.exception.code, "invalid_input")

    def test_p4c_facts_remain_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp, clock=lambda: CLOCK)
            ledger.append_decision(_machine(), _provenance())
            ledger.persist_retry_scope_started(
                SESSION_ID,
                retry_scope_id="m1-retry-scope-" + ("cd" * 32),
                source_provenance=_provenance(),
            )
            result = ledger.replay_session(SESSION_ID)
            self.assertEqual(result.p4c_facts[0].event_type, "retry_scope_started")
            self.assertFalse(hasattr(result, "retry_scope_state"))
            self.assertEqual(result.views[0].replayed_action, "accept")
            self.assertNotIn("retry_count", dict(result.views[0].provenance))
