"""P4B-D replay / integrity probes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from digital_pulse.m1_contracts import (
    DecisionAction,
    DecisionInputVersions,
    M1Decision,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import (
    ALREADY_COMMITTED,
    DecisionLedger,
    DecisionSourceProvenance,
    LedgerReplayResult,
    M1IntError,
    build_int_ledger_event,
    fold_ledger_snapshot,
)
from digital_pulse.m1_int.ledger_models import LEDGER_SCHEMA_VERSION
from digital_pulse.m1_int.models import dumps_canonical
from digital_pulse.m1_int.replay_models import LedgerSnapshot
from digital_pulse.m1_int.rules import I1RuleEngine

SESSION = "session-p4b-d"
DECISION = "m1-decision-" + ("aa" * 32)
SOFTWARE = "a" * 40
CONFIG = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
SCOPE = "m1-retry-scope-" + ("ab" * 32)
FAKE_DECISION = "m1-decision-" + ("ff" * 32)


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
        reason_codes=("quality_acceptable",),
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


def _provenance(software: str = SOFTWARE) -> DecisionSourceProvenance:
    return DecisionSourceProvenance(
        app_run_id="run-p4b-d",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=software,
    )


def _ledger(root: Path, clock=None, injector=None) -> DecisionLedger:
    return DecisionLedger(root, clock=clock or (lambda: CLOCK), failure_injector=injector)


def _int_dir(root: Path) -> Path:
    return root / SESSION / "int"


def _event_line(event) -> bytes:
    payload = {item.name: getattr(event, item.name) for item in fields(event) if getattr(event, item.name) is not None}
    return (dumps_canonical(payload) + "\n").encode("utf-8")


def _append_adversarial_event(root: Path, **kwargs) -> None:
    events_path = _int_dir(root) / "decision-events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    next_seq = records[-1]["event_seq"] + 1 if records else 1
    kwargs.setdefault("event_seq", next_seq)
    kwargs.setdefault("session_id", SESSION)
    kwargs.setdefault("occurred_at_utc", CLOCK)
    event = build_int_ledger_event(**kwargs)
    with events_path.open("ab") as handle:
        handle.write(_event_line(event))


def _cross_process_replay(root: str) -> str:
    ledger = DecisionLedger(Path(root), clock=lambda: CLOCK)
    return ledger.replay_session(SESSION).replay_fingerprint


class ReplayIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, action: DecisionAction = DecisionAction.ACCEPT) -> DecisionLedger:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(action), _provenance())
        return ledger

    def test_01_machine_only(self) -> None:
        ledger = self._seed()
        result = ledger.replay_session(SESSION)
        self.assertIsInstance(result, LedgerReplayResult)
        view = result.views[0]
        self.assertEqual(view.machine_action, "accept")
        self.assertEqual(view.replayed_action, "accept")
        self.assertIsNone(view.outcome)
        self.assertFalse(view.completed)

    def test_02_awaiting(self) -> None:
        ledger = self._seed(DecisionAction.MANUAL_REVIEW)
        view = ledger.replay_session(SESSION).views[0]
        self.assertTrue(view.awaiting_operator)
        self.assertEqual(view.outcome, "awaiting_operator")

    def test_03_allowed_override(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="stop", source_provenance=_provenance()
        )
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.machine_action, "accept")
        self.assertEqual(view.replayed_action, "stop")

    def test_04_rejected_override_not_effective(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="retry_same_position",
            operator_id="op-1",
            note="illegal",
            source_provenance=_provenance(),
        )
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.replayed_action, "accept")
        self.assertEqual(len(view.rejection_facts), 1)
        self.assertIsNone(view.derived_action_at_apply)

    def test_05_applied_outcome(self) -> None:
        ledger = self._seed()
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.outcome, "applied")
        self.assertEqual(view.derived_action_at_apply, "accept")
        self.assertFalse(hasattr(view, "persisted_applied_action"))

    def test_06_completed(self) -> None:
        ledger = self._seed()
        ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        view = ledger.replay_session(SESSION).views[0]
        self.assertTrue(view.completed)
        self.assertEqual(view.outcome, "completed")

    def test_07_remain_awaiting(self) -> None:
        ledger = self._seed(DecisionAction.MANUAL_REVIEW)
        ledger.persist_manual_review_resolution(
            SESSION, DECISION, resolution="remain_awaiting", operator_id="op-1", source_provenance=_provenance()
        )
        view = ledger.replay_session(SESSION).views[0]
        self.assertTrue(view.awaiting_operator)
        self.assertEqual(view.manual_review_resolution, "remain_awaiting")
        self.assertEqual(view.replayed_action, "manual_review")

    def test_08_terminate_stop_does_not_rewrite_action(self) -> None:
        ledger = self._seed(DecisionAction.MANUAL_REVIEW)
        ledger.persist_manual_review_resolution(
            SESSION, DECISION, resolution="terminate_stop", operator_id="op-1", source_provenance=_provenance()
        )
        view = ledger.replay_session(SESSION).views[0]
        self.assertFalse(view.awaiting_operator)
        self.assertEqual(view.replayed_action, "manual_review")

    def test_09_continue_new_acquisition_is_fact_only(self) -> None:
        ledger = self._seed(DecisionAction.MANUAL_REVIEW)
        ledger.persist_manual_review_resolution(
            SESSION,
            DECISION,
            resolution="continue_new_acquisition",
            operator_id="op-1",
            source_provenance=_provenance(),
        )
        result = ledger.replay_session(SESSION)
        self.assertEqual(result.views[0].manual_review_resolution, "continue_new_acquisition")
        self.assertFalse((self.root / "new-session").exists())

    def test_10_same_ledger_same_replay(self) -> None:
        ledger = self._seed()
        first = ledger.replay_session(SESSION)
        second = ledger.replay_session(SESSION)
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)
        self.assertEqual(first.views, second.views)

    def test_11_runtime_wall_clock_irrelevant(self) -> None:
        self._seed()
        first = _ledger(self.root, clock=lambda: "2026-01-01T00:00:00Z").replay_session(SESSION)
        later = time.time()
        second = _ledger(self.root, clock=lambda: "2099-12-31T23:59:59Z").replay_session(SESSION)
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)
        self.assertGreater(later, 0)

    def test_12_path_irrelevant(self) -> None:
        ledger = self._seed()
        first = ledger.replay_session(SESSION)
        copied = self.root / "copy-root"
        shutil.copytree(self.root / SESSION, copied / SESSION)
        second = _ledger(copied).replay_session(SESSION)
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)

    def test_13_malformed_decision(self) -> None:
        self._seed()
        (_int_dir(self.root) / "decisions.jsonl").write_text("{not-json}\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_14_malformed_event(self) -> None:
        self._seed()
        (_int_dir(self.root) / "decision-events.jsonl").write_text("{not-json}\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_15_unrecoverable_partial_tail(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "decision-events.jsonl"
        path.write_bytes(path.read_bytes() + b'{"event_type":"broken"')
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_16_seq_gap(self) -> None:
        self._seed()
        _append_adversarial_event(self.root, event_type="action_applied", decision_id=DECISION, outcome="applied", event_seq=3)
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_17_duplicate_seq(self) -> None:
        self._seed()
        _append_adversarial_event(self.root, event_type="action_applied", decision_id=DECISION, outcome="applied", event_seq=1)
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_18_reorder(self) -> None:
        ledger = self._seed()
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        path = _int_dir(self.root) / "decision-events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(lines[1] + "\n" + lines[0] + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_19_dangling_decision_reference(self) -> None:
        self._seed()
        _append_adversarial_event(
            self.root, event_type="action_applied", decision_id=FAKE_DECISION, outcome="applied"
        )
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "dangling_decision_reference")

    def test_20_missing_decision_recorded(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "decision-events.jsonl"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertIn(raised.exception.code, {"decision_record_mismatch", "ledger_untrusted"})

    def test_21_decision_record_mismatch(self) -> None:
        self._seed()
        events = _int_dir(self.root) / "decision-events.jsonl"
        events.write_text("", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "decision_record_mismatch")

    def test_22_stale_manifest_count_reconcile(self) -> None:
        ledger = self._seed()
        path = _int_dir(self.root) / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["decision_count"] = 99
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = ledger.replay_session(SESSION)
        self.assertEqual(result.integrity_status, "trusted")

    def test_23_stale_digest_reconcile(self) -> None:
        ledger = self._seed()
        path = _int_dir(self.root) / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["events_sha256"] = "ab" * 32
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = ledger.replay_session(SESSION)
        self.assertEqual(result.integrity_status, "trusted")

    def test_24_missing_manifest_reconcile(self) -> None:
        ledger = self._seed()
        (_int_dir(self.root) / "manifest.json").unlink()
        result = ledger.replay_session(SESSION)
        self.assertEqual(result.integrity_status, "trusted")

    def test_25_unreconcilable_final_mismatch(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["events_sha256"] = "ab" * 32
        path.write_text(json.dumps(payload), encoding="utf-8")
        ledger = _ledger(self.root)
        ledger._reconcile_manifest = lambda *args, **kwargs: None  # type: ignore[method-assign]
        with self.assertRaises(M1IntError) as raised:
            ledger.replay_session(SESSION)
        self.assertEqual(raised.exception.code, "manifest_mismatch")

    def test_26_unsupported_schema(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "decision-events.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        record["ledger_schema_version"] = "not-a-schema"
        path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertIn(raised.exception.code, {"unsupported_schema_version", "ledger_untrusted"})

    def test_27_unknown_event(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "decision-events.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        record["event_type"] = "not_a_frozen_type"
        path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertIn(raised.exception.code, {"unsupported_event_type", "ledger_untrusted"})

    def test_28_completed_then_applied(self) -> None:
        ledger = self._seed()
        ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        with self.assertRaises(M1IntError) as raised:
            ledger.replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_29_completed_then_override(self) -> None:
        ledger = self._seed()
        ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="late", source_provenance=_provenance()
        )
        with self.assertRaises(M1IntError) as raised:
            ledger.replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_30_repeated_completed_adversarial(self) -> None:
        ledger = self._seed()
        ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        _append_adversarial_event(self.root, event_type="decision_completed", decision_id=DECISION, outcome="completed")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_31_repeated_applied_adversarial(self) -> None:
        ledger = self._seed()
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        _append_adversarial_event(self.root, event_type="action_applied", decision_id=DECISION, outcome="applied")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_32_conflicting_override(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="a", source_provenance=_provenance()
        )
        _append_adversarial_event(
            self.root,
            event_type="operator_override",
            decision_id=DECISION,
            requested_action="manual_review",
            operator_id="op-1",
            note="b",
        )
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_33_invalid_manual_review_order(self) -> None:
        ledger = self._seed()
        ledger.persist_manual_review_resolution(
            SESSION, DECISION, resolution="remain_awaiting", operator_id="op-1", source_provenance=_provenance()
        )
        with self.assertRaises(M1IntError) as raised:
            ledger.replay_session(SESSION)
        self.assertEqual(raised.exception.code, "lifecycle_conflict")

    def test_34_rejected_override_not_effective_duplicate(self) -> None:
        self.test_04_rejected_override_not_effective()

    def test_35_action_applied_does_not_invent_action(self) -> None:
        ledger = self._seed()
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        result = ledger.replay_session(SESSION)
        applied = [item for item in result.events if item.event_type == "action_applied"][0]
        self.assertIsNone(applied.requested_action)
        self.assertEqual(applied.outcome, "applied")
        self.assertEqual(result.views[0].replayed_action, "accept")

    def test_36_derived_action_at_apply_uses_prior_fold(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="stop", source_provenance=_provenance()
        )
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.derived_action_at_apply, "stop")
        self.assertEqual(view.machine_action, "accept")

    def test_37_decision_recorded_provenance_preserved(self) -> None:
        ledger = self._seed()
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.provenance["software_commit_sha"], SOFTWARE)
        self.assertEqual(view.provenance["app_run_id"], "run-p4b-d")

    def test_38_malformed_provenance(self) -> None:
        self._seed()
        path = _int_dir(self.root) / "decision-events.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        record["software_commit_sha"] = "not-a-sha"
        path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).replay_session(SESSION)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_39_writer_idempotency_provenance_regression(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="stop", source_provenance=_provenance()
        )
        with self.assertRaises(M1IntError) as raised:
            ledger.persist_operator_override(
                SESSION,
                DECISION,
                requested_action="stop",
                operator_id="op-1",
                note="stop",
                source_provenance=_provenance("b" * 40),
            )
        self.assertEqual(raised.exception.code, "provenance_mismatch")
        retry = ledger.persist_operator_override(
            SESSION, DECISION, requested_action="stop", operator_id="op-1", note="stop", source_provenance=_provenance()
        )
        self.assertEqual(retry.status, ALREADY_COMMITTED)

    def test_40_replay_never_calls_p4a(self) -> None:
        ledger = self._seed()

        def boom(*args, **kwargs):
            raise AssertionError("I1RuleEngine must not run during replay")

        original = I1RuleEngine.evaluate
        I1RuleEngine.evaluate = boom
        try:
            result = ledger.replay_session(SESSION)
        finally:
            I1RuleEngine.evaluate = original
        self.assertEqual(result.integrity_status, "trusted")

    def test_41_oracle_isolation_source(self) -> None:
        replay = Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_int" / "replay.py"
        models = replay.with_name("replay_models.py")
        text = replay.read_text(encoding="utf-8") + models.read_text(encoding="utf-8")
        for needle in ("expected_int_action", "expected_quality_label", "scenario.json", "expected.json"):
            self.assertNotIn(needle, text)

    def test_42_no_retryscope_semantics(self) -> None:
        ledger = self._seed()
        ledger.persist_retry_scope_started(SESSION, retry_scope_id=SCOPE, source_provenance=_provenance())
        result = ledger.replay_session(SESSION)
        self.assertEqual(len(result.p4c_facts), 1)
        self.assertEqual(result.p4c_facts[0].event_type, "retry_scope_started")
        self.assertFalse(hasattr(result, "retry_scope_state"))

    def test_43_no_retry_budget(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_int" / "replay.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("max_retry_count", source)
        self.assertNotIn("retry_count + 1", source)

    def test_44_no_scheduling(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_int" / "replay.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("schedule", source)

    def test_45_no_reposition_ack_generation(self) -> None:
        ledger = self._seed(DecisionAction.REPOSITION)
        before = (_int_dir(self.root) / "decision-events.jsonl").read_bytes()
        ledger.replay_session(SESSION)
        after = (_int_dir(self.root) / "decision-events.jsonl").read_bytes()
        self.assertEqual(before, after)
        self.assertNotIn(b"reposition_acknowledged", after)

    def test_46_no_report_write(self) -> None:
        self._seed()
        _ledger(self.root).replay_session(SESSION)
        self.assertFalse((self.root / SESSION / "app").exists())
        self.assertFalse((_int_dir(self.root) / "reports").exists())

    def test_47_no_hardware(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_int" / "replay.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("hardware", source.lower())

    def test_48_no_torn_snapshot(self) -> None:
        ledger = self._seed()

        def writer() -> None:
            ledger.persist_operator_override(
                SESSION, DECISION, requested_action="stop", operator_id="op-1", note="stop", source_provenance=_provenance()
            )

        def reader() -> LedgerReplayResult:
            return ledger.replay_session(SESSION)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_w = pool.submit(writer)
            future_r = pool.submit(reader)
            future_w.result()
            result = future_r.result()
        self.assertIn(result.last_event_seq, {1, 2})
        self.assertEqual(result.events[-1].event_seq, result.last_event_seq)

    def test_49_thread_lock_regression(self) -> None:
        ledger = self._seed()

        def replay() -> str:
            return ledger.replay_session(SESSION).replay_fingerprint

        with ThreadPoolExecutor(max_workers=3) as pool:
            fingerprints = {pool.submit(replay).result() for _ in range(3)}
        self.assertEqual(len(fingerprints), 1)

    def test_50_cross_process_lock_regression(self) -> None:
        self._seed()
        context = multiprocessing.get_context("spawn")
        with context.Pool(2) as pool:
            fingerprints = pool.map(_cross_process_replay, [str(self.root), str(self.root)])
        self.assertEqual(fingerprints[0], fingerprints[1])

    def test_business_read_only_steady_state(self) -> None:
        ledger = self._seed()
        decisions = (_int_dir(self.root) / "decisions.jsonl").read_bytes()
        events = (_int_dir(self.root) / "decision-events.jsonl").read_bytes()
        ledger.replay_session(SESSION)
        self.assertEqual((_int_dir(self.root) / "decisions.jsonl").read_bytes(), decisions)
        self.assertEqual((_int_dir(self.root) / "decision-events.jsonl").read_bytes(), events)

    def test_recovery_replay_no_extra_business_fact(self) -> None:
        seed = _ledger(self.root)
        seed.append_decision(_decision(), _provenance())
        try:
            _ledger(self.root, injector=_fail_at("events_append")).persist_action_applied(
                SESSION, DECISION, source_provenance=_provenance()
            )
        except M1IntError as exc:
            self.assertEqual(exc.code, "persistence_failure")
        result = _ledger(self.root).replay_session(SESSION)
        applied = [item for item in result.events if item.event_type == "action_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(result.views[0].outcome, "applied")

    def test_snapshot_forgery_rejected(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            LedgerSnapshot(
                session_id=SESSION,
                machine_decisions=(),
                events=(),
                ledger_schema_version=LEDGER_SCHEMA_VERSION,
                manifest_schema_version="x",
                decisions_sha256="ab" * 32,
                events_sha256="cd" * 32,
                last_event_seq=0,
                _token=object(),
            )
        self.assertEqual(raised.exception.code, "invalid_input")
        with self.assertRaises(M1IntError):
            fold_ledger_snapshot(object())  # type: ignore[arg-type]

    def test_rejected_cannot_become_derived_apply(self) -> None:
        ledger = self._seed()
        ledger.persist_operator_override(
            SESSION,
            DECISION,
            requested_action="retry_same_position",
            operator_id="op-1",
            note="illegal",
            source_provenance=_provenance(),
        )
        ledger.persist_action_applied(SESSION, DECISION, source_provenance=_provenance())
        view = ledger.replay_session(SESSION).views[0]
        self.assertEqual(view.derived_action_at_apply, "accept")
        self.assertNotEqual(view.derived_action_at_apply, "retry_same_position")

    def test_p4c_facts_after_completed_are_raw(self) -> None:
        ledger = self._seed()
        ledger.persist_decision_completed(SESSION, DECISION, source_provenance=_provenance())
        ledger.persist_retry_scope_started(SESSION, retry_scope_id=SCOPE, source_provenance=_provenance())
        result = ledger.replay_session(SESSION)
        self.assertTrue(result.views[0].completed)
        self.assertEqual(result.p4c_facts[0].event_type, "retry_scope_started")


def _fail_at(point: str):
    def injector(actual: str) -> None:
        if actual == point:
            raise M1IntError("persistence_failure", f"injected failure at {actual}")

    return injector


if __name__ == "__main__":
    unittest.main()
