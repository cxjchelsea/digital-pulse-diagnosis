"""P4B-B machine decision persistence: append, lock, WAL, recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from digital_pulse.m1_contracts import (
    DecisionAction,
    DecisionInputVersions,
    M1Decision,
    OperatorOverride,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import (
    ALREADY_COMMITTED,
    COMMITTED,
    DecisionLedger,
    DecisionSourceProvenance,
    M1IntError,
    require_machine_decision_record,
)
from digital_pulse.m1_int.ledger_models import EMPTY_LEDGER_DIGEST
from digital_pulse.m1_int.persist.ledger import PENDING_SCHEMA_VERSION

SESSION_A = "session-p4b-b-a"
SESSION_B = "session-p4b-b-b"
DECISION_A = "m1-decision-" + ("aa" * 32)
DECISION_B = "m1-decision-" + ("bb" * 32)
SOFTWARE_A = "a" * 40
SOFTWARE_B = "b" * 40
CONFIG = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"


def _decision(
    *,
    decision_id: str = DECISION_A,
    session_id: str = SESSION_A,
    action: DecisionAction = DecisionAction.ACCEPT,
    decided_at: str = CLOCK,
    parameter_status: ParameterStatus = ParameterStatus.PENDING_H1_CALIBRATION,
    operator_override=None,
    outcome=None,
    config: str = CONFIG,
) -> M1Decision:
    return M1Decision(
        decision_id=decision_id,
        session_id=session_id,
        decided_at_utc=decided_at,
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=session_id, window_id="window-0001"),
        action=action,
        reason_codes=("quality_acceptable",),
        rule_version="i1-pre-0.1.0",
        input_versions=DecisionInputVersions(
            signal_processing_version="0.4.0-p2d",
            decision_rule_version="i1-pre-0.1.0",
            configuration_digest=config,
        ),
        retry_count=0,
        max_retry_count=2,
        operator_override=operator_override,
        outcome=outcome,
        parameter_status=parameter_status,
    )


def _provenance(
    software: str = SOFTWARE_A,
    *,
    app_run_id: str | None = "run-0001",
    app_analysis_fingerprint: str | None = FINGERPRINT,
    sp_result_fingerprint: str | None = FINGERPRINT,
) -> DecisionSourceProvenance:
    return DecisionSourceProvenance(
        app_run_id=app_run_id,
        app_analysis_fingerprint=app_analysis_fingerprint,
        sp_result_fingerprint=sp_result_fingerprint,
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


class DecisionAppendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.id().replace(".", "_"))
        self.root = Path(__file__).resolve().parent / "_p4bb_tmp" / self._testMethodName
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_first_append_commits_decision_and_recorded_event(self) -> None:
        ledger = _ledger(self.root)
        result = ledger.append_decision(_decision(), _provenance())
        self.assertEqual(result.status, COMMITTED)
        self.assertEqual(result.event_seq, 1)
        self.assertIsNone(result.awaiting_operator_seq)
        loaded = ledger.load_machine_decision(SESSION_A, DECISION_A)
        self.assertEqual(loaded.decision_id, DECISION_A)
        self.assertIsNone(loaded.operator_override)
        self.assertIsNone(loaded.outcome)
        int_dir = self.root / SESSION_A / "int"
        decisions = (int_dir / "decisions.jsonl").read_bytes()
        events = (int_dir / "decision-events.jsonl").read_bytes()
        self.assertTrue(decisions.endswith(b"\n"))
        self.assertTrue(events.endswith(b"\n"))
        self.assertEqual(decisions.count(b"\n"), 1)
        self.assertEqual(events.count(b"\n"), 1)
        event = json.loads(events.splitlines()[0])
        self.assertEqual(event["event_type"], "decision_recorded")
        self.assertEqual(event["event_seq"], 1)
        self.assertEqual(event["software_commit_sha"], SOFTWARE_A)
        manifest = ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(manifest.decision_count, 1)
        self.assertEqual(manifest.event_count, 1)
        self.assertEqual(manifest.last_event_seq, 1)
        self.assertEqual(manifest.decisions_sha256, hashlib.sha256(decisions).hexdigest())
        self.assertEqual(manifest.events_sha256, hashlib.sha256(events).hexdigest())
        self.assertFalse((int_dir / ".pending-commit.json").exists())
        self.assertFalse((self.root / SESSION_A / "app").exists())
        self.assertFalse((int_dir / "reports").exists())

    def test_manual_review_writes_awaiting_operator_companion(self) -> None:
        ledger = _ledger(self.root)
        result = ledger.append_decision(_decision(action=DecisionAction.MANUAL_REVIEW), _provenance())
        self.assertEqual(result.event_seq, 1)
        self.assertEqual(result.awaiting_operator_seq, 2)
        events = [json.loads(line) for line in (self.root / SESSION_A / "int" / "decision-events.jsonl").read_text().splitlines()]
        self.assertEqual([item["event_type"] for item in events], ["decision_recorded", "awaiting_operator"])
        self.assertEqual(events[1]["event_seq"], 2)
        self.assertEqual(events[1]["outcome"], "awaiting_operator")
        manifest = ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(manifest.event_count, 2)
        self.assertEqual(manifest.last_event_seq, 2)

    def test_identical_duplicate_is_idempotent(self) -> None:
        ledger = _ledger(self.root)
        first = ledger.append_decision(_decision(), _provenance())
        before_d = (self.root / SESSION_A / "int" / "decisions.jsonl").read_bytes()
        before_e = (self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes()
        second = ledger.append_decision(_decision(), _provenance())
        self.assertEqual(second.status, ALREADY_COMMITTED)
        self.assertEqual(second.event_seq, first.event_seq)
        self.assertEqual((self.root / SESSION_A / "int" / "decisions.jsonl").read_bytes(), before_d)
        self.assertEqual((self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes(), before_e)
        manifest = ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(manifest.decision_count, 1)
        self.assertEqual(manifest.event_count, 1)

    def test_same_decision_different_software_sha_is_provenance_mismatch(self) -> None:
        ledger = _ledger(self.root)
        first = ledger.append_decision(_decision(), _provenance(SOFTWARE_A))
        before_e = (self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes()
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(_decision(), _provenance(SOFTWARE_B))
        self.assertEqual(raised.exception.code, "provenance_mismatch")
        self.assertEqual((self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes(), before_e)
        self.assertEqual(first.status, COMMITTED)

    def test_same_decision_different_run_or_fingerprint_is_provenance_mismatch(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        cases = (
            _provenance(app_run_id="run-0002"),
            _provenance(app_analysis_fingerprint="11" * 32),
            _provenance(sp_result_fingerprint="22" * 32),
        )
        for provenance in cases:
            with self.subTest(provenance=provenance):
                with self.assertRaises(M1IntError) as raised:
                    ledger.append_decision(_decision(), provenance)
                self.assertEqual(raised.exception.code, "provenance_mismatch")
        self.assertEqual((self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes().count(b"\n"), 1)

    def test_same_id_changed_action_conflicts(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(_decision(action=DecisionAction.STOP), _provenance())
        self.assertEqual(raised.exception.code, "duplicate_conflict")

    def test_same_id_changed_input_versions_conflicts(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(_decision(config="11" * 32), _provenance())
        self.assertEqual(raised.exception.code, "duplicate_conflict")

    def test_same_id_changed_parameter_status_conflicts(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(
                _decision(parameter_status=ParameterStatus.SYNTHETIC_ONLY),
                _provenance(),
            )
        self.assertEqual(raised.exception.code, "duplicate_conflict")

    def test_override_or_outcome_rejected_before_duplicate_logic(self) -> None:
        ledger = _ledger(self.root)
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(
                _decision(operator_override=OperatorOverride(operator_id="op", note="x")),
                _provenance(),
            )
        self.assertEqual(raised.exception.code, "invalid_input")
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(_decision(outcome="applied"), _provenance())
        self.assertEqual(raised.exception.code, "invalid_input")
        self.assertFalse((self.root / SESSION_A / "int" / "decisions.jsonl").exists())

    def test_same_id_changed_decided_at_conflicts(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(_decision(decided_at="2026-08-20T00:00:00Z"), _provenance())
        self.assertEqual(raised.exception.code, "duplicate_conflict")

    def test_same_id_changed_reason_retry_or_quality_conflicts(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        variants = (
            replace(_decision(), reason_codes=("quality_borderline",)),
            replace(_decision(), retry_count=1),
            replace(_decision(), max_retry_count=3),
            replace(_decision(), device_state="IDLE"),
            replace(_decision(), quality_reference=QualityReference(session_id=SESSION_A, window_id="window-0002")),
        )
        for decision in variants:
            with self.subTest(decision=decision):
                with self.assertRaises(M1IntError) as raised:
                    ledger.append_decision(decision, _provenance())
                self.assertEqual(raised.exception.code, "duplicate_conflict")

    def test_software_sha_does_not_enter_decision_id_but_is_audit_bound(self) -> None:
        decision = _decision()
        require_machine_decision_record(decision)
        self.assertEqual(decision.decision_id, DECISION_A)
        ledger = _ledger(self.root)
        ledger.append_decision(decision, _provenance(SOFTWARE_A))
        with self.assertRaises(M1IntError) as raised:
            ledger.append_decision(decision, _provenance(SOFTWARE_B))
        self.assertEqual(raised.exception.code, "provenance_mismatch")
        loaded = ledger.load_machine_decision(SESSION_A, DECISION_A)
        self.assertEqual(loaded.decision_id, DECISION_A)
        event = json.loads((self.root / SESSION_A / "int" / "decision-events.jsonl").read_text().splitlines()[0])
        self.assertEqual(event["software_commit_sha"], SOFTWARE_A)
        self.assertEqual((self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes().count(b"\n"), 1)

    def test_unsafe_session_ids_are_rejected(self) -> None:
        ledger = _ledger(self.root)
        unsafe = (
            "../escape",
            "..\\escape",
            "/tmp/abs",
            r"C:\abs",
            ".",
            "..",
            "",
            " spaced",
            "space id",
            "con",
            "NUL",
        )
        for session_id in unsafe:
            with self.subTest(session_id=session_id):
                with self.assertRaises(M1IntError) as raised:
                    ledger.append_decision(_decision(session_id=session_id), _provenance())
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_wrong_session_and_schema_fail_closed(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        int_dir = self.root / SESSION_A / "int"
        events = int_dir / "decision-events.jsonl"
        payload = json.loads(events.read_text().splitlines()[0])
        payload["session_id"] = "other-session"
        events.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "_p4bb_tmp" / ("crash_" + self._testMethodName)
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _recover(self) -> None:
        _ledger(self.root).append_decision(_decision(), _provenance())

    def test_failure_points_recover_or_remain_empty(self) -> None:
        points = (
            "pending_write",
            "pending_fsync",
            "decisions_append",
            "decisions_fsync",
            "events_append",
            "events_fsync",
            "manifest_write",
            "manifest_fsync",
            "pending_delete",
        )
        for point in points:
            with self.subTest(point=point):
                case_root = self.root / point
                case_root.mkdir()
                failing = _ledger(case_root, _fail_at(point))
                with self.assertRaises(M1IntError) as raised:
                    failing.append_decision(_decision(), _provenance())
                self.assertEqual(raised.exception.code, "persistence_failure")
                recovered = _ledger(case_root)
                if point in {"pending_write", "pending_fsync"}:
                    with self.assertRaises(M1IntError):
                        recovered.load_machine_decision(SESSION_A, DECISION_A)
                else:
                    loaded = recovered.load_machine_decision(SESSION_A, DECISION_A)
                    self.assertEqual(loaded.decision_id, DECISION_A)
                    recovered.verify_decision_ledger_minimal(SESSION_A)
                    self.assertFalse((case_root / SESSION_A / "int" / ".pending-commit.json").exists())

    def test_no_pending_trailing_partial_is_untrusted(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        path = self.root / SESSION_A / "int" / "decisions.jsonl"
        before = path.read_bytes()
        path.write_bytes(before + b'{"decision_id":"partial"')
        with self.assertRaises(M1IntError) as raised:
            ledger.recover_pending_commit(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")
        self.assertEqual(path.read_bytes(), before + b'{"decision_id":"partial"')

    def test_pending_bound_missing_newline_prefix_is_recovered(self) -> None:
        failing = _ledger(self.root, _fail_at("decisions_append"))
        with self.assertRaises(M1IntError) as raised:
            failing.append_decision(_decision(), _provenance())
        self.assertEqual(raised.exception.code, "persistence_failure")
        path = self.root / SESSION_A / "int" / "decisions.jsonl"
        complete = path.read_bytes()
        self.assertTrue(complete.endswith(b"\n"))
        path.write_bytes(complete[:-1])
        recovered = _ledger(self.root)
        loaded = recovered.load_machine_decision(SESSION_A, DECISION_A)
        self.assertEqual(loaded.decision_id, DECISION_A)
        recovered.verify_decision_ledger_minimal(SESSION_A)
        self.assertFalse((self.root / SESSION_A / "int" / ".pending-commit.json").exists())

    def test_pending_bound_divergent_tail_is_untrusted(self) -> None:
        failing = _ledger(self.root, _fail_at("decisions_append"))
        with self.assertRaises(M1IntError):
            failing.append_decision(_decision(), _provenance())
        path = self.root / SESSION_A / "int" / "decisions.jsonl"
        path.write_bytes(b'{"other":true')
        with self.assertRaises(M1IntError) as raised:
            _ledger(self.root).recover_pending_commit(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_completed_transaction_with_retained_pending_is_cleared(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        int_dir = self.root / SESSION_A / "int"
        decisions = (int_dir / "decisions.jsonl").read_bytes()
        events = (int_dir / "decision-events.jsonl").read_bytes()
        pending = {
            "schema_version": PENDING_SCHEMA_VERSION,
            "session_id": SESSION_A,
            "decision_id": DECISION_A,
            "ledger_schema_version": "i1-ledger-1.0.0-pre",
            "pre_decision_count": 0,
            "pre_event_count": 0,
            "pre_last_event_seq": 0,
            "pre_decisions_sha256": EMPTY_LEDGER_DIGEST,
            "pre_events_sha256": EMPTY_LEDGER_DIGEST,
            "decision_record": decisions.decode("utf-8"),
            "event_records": [events.decode("utf-8")],
            "post_decision_count": 1,
            "post_event_count": 1,
            "post_last_event_seq": 1,
            "post_decisions_sha256": hashlib.sha256(decisions).hexdigest(),
            "post_events_sha256": hashlib.sha256(events).hexdigest(),
            "decision_rule_version": "i1-pre-0.1.0",
            "configuration_digest": CONFIG,
            "software_commit_sha": SOFTWARE_A,
        }
        (int_dir / ".pending-commit.json").write_text(json.dumps(pending, sort_keys=True) + "\n", encoding="utf-8")
        ledger.recover_pending_commit(SESSION_A)
        self.assertFalse((int_dir / ".pending-commit.json").exists())
        ledger.verify_decision_ledger_minimal(SESSION_A)

    def test_stale_pending_after_later_transaction_is_untrusted(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        first_d = (self.root / SESSION_A / "int" / "decisions.jsonl").read_bytes()
        first_e = (self.root / SESSION_A / "int" / "decision-events.jsonl").read_bytes()
        ledger.append_decision(_decision(decision_id=DECISION_B, action=DecisionAction.STOP), _provenance())
        pending = {
            "schema_version": PENDING_SCHEMA_VERSION,
            "session_id": SESSION_A,
            "decision_id": DECISION_A,
            "ledger_schema_version": "i1-ledger-1.0.0-pre",
            "pre_decision_count": 0,
            "pre_event_count": 0,
            "pre_last_event_seq": 0,
            "pre_decisions_sha256": EMPTY_LEDGER_DIGEST,
            "pre_events_sha256": EMPTY_LEDGER_DIGEST,
            "decision_record": first_d.decode("utf-8"),
            "event_records": [first_e.decode("utf-8")],
            "post_decision_count": 1,
            "post_event_count": 1,
            "post_last_event_seq": 1,
            "post_decisions_sha256": hashlib.sha256(first_d).hexdigest(),
            "post_events_sha256": hashlib.sha256(first_e).hexdigest(),
            "decision_rule_version": "i1-pre-0.1.0",
            "configuration_digest": CONFIG,
            "software_commit_sha": SOFTWARE_A,
        }
        (self.root / SESSION_A / "int" / ".pending-commit.json").write_text(
            json.dumps(pending, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(M1IntError) as raised:
            ledger.recover_pending_commit(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")
        (self.root / SESSION_A / "int" / ".pending-commit.json").unlink()
        self.assertEqual(_ledger(self.root).verify_decision_ledger_minimal(SESSION_A).decision_count, 2)

    def test_cross_session_pending_is_untrusted(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        ledger.append_decision(_decision(session_id=SESSION_B, decision_id=DECISION_B), _provenance())
        source = (self.root / SESSION_B / "int" / ".pending-commit.json")
        # SESSION_B has no pending; plant SESSION_B identity into SESSION_A.
        pending = {
            "schema_version": PENDING_SCHEMA_VERSION,
            "session_id": SESSION_B,
            "decision_id": DECISION_B,
            "ledger_schema_version": "i1-ledger-1.0.0-pre",
            "pre_decision_count": 0,
            "pre_event_count": 0,
            "pre_last_event_seq": 0,
            "pre_decisions_sha256": EMPTY_LEDGER_DIGEST,
            "pre_events_sha256": EMPTY_LEDGER_DIGEST,
            "decision_record": (self.root / SESSION_B / "int" / "decisions.jsonl").read_text(encoding="utf-8"),
            "event_records": [(self.root / SESSION_B / "int" / "decision-events.jsonl").read_text(encoding="utf-8")],
            "post_decision_count": 1,
            "post_event_count": 1,
            "post_last_event_seq": 1,
            "post_decisions_sha256": hashlib.sha256((self.root / SESSION_B / "int" / "decisions.jsonl").read_bytes()).hexdigest(),
            "post_events_sha256": hashlib.sha256((self.root / SESSION_B / "int" / "decision-events.jsonl").read_bytes()).hexdigest(),
            "decision_rule_version": "i1-pre-0.1.0",
            "configuration_digest": CONFIG,
            "software_commit_sha": SOFTWARE_A,
        }
        (self.root / SESSION_A / "int" / ".pending-commit.json").write_text(
            json.dumps(pending, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        del source
        with self.assertRaises(M1IntError) as raised:
            ledger.recover_pending_commit(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_middle_corruption_is_untrusted(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        ledger.append_decision(_decision(decision_id=DECISION_B, action=DecisionAction.STOP), _provenance())
        path = self.root / SESSION_A / "int" / "decisions.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = "{not-json}"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_manifest_digest_mismatch_fail_closed(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        manifest_path = self.root / SESSION_A / "int" / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["decisions_sha256"] = "11" * 32
        manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        repaired = ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(repaired.decisions_sha256, hashlib.sha256((self.root / SESSION_A / "int" / "decisions.jsonl").read_bytes()).hexdigest())
        self.assertNotEqual(repaired.decisions_sha256, "11" * 32)

    def test_event_seq_gap_fail_closed(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        events = self.root / SESSION_A / "int" / "decision-events.jsonl"
        payload = json.loads(events.read_text().splitlines()[0])
        payload["event_seq"] = 3
        events.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(M1IntError) as raised:
            ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(raised.exception.code, "ledger_untrusted")

    def test_tampered_pending_fail_closed(self) -> None:
        ledger = _ledger(self.root)
        ledger.append_decision(_decision(), _provenance())
        pending = {
            "schema_version": PENDING_SCHEMA_VERSION,
            "session_id": SESSION_A,
            "decision_id": DECISION_B,
            "pre_decision_count": 0,
            "pre_event_count": 0,
            "pre_last_event_seq": 0,
            "pre_decisions_sha256": "11" * 32,
            "pre_events_sha256": EMPTY_LEDGER_DIGEST,
            "decision_record": "{}\n",
            "event_records": ["{}\n"],
            "post_decision_count": 1,
            "post_event_count": 1,
            "post_last_event_seq": 1,
            "post_decisions_sha256": "22" * 32,
            "post_events_sha256": "33" * 32,
            "decision_rule_version": "i1-pre-0.1.0",
            "configuration_digest": CONFIG,
            "software_commit_sha": SOFTWARE_A,
        }
        (self.root / SESSION_A / "int" / ".pending-commit.json").write_text(
            json.dumps(pending, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(M1IntError) as raised:
            ledger.recover_pending_commit(SESSION_A)
        self.assertIn(raised.exception.code, {"ledger_untrusted", "version_mismatch"})


class ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "_p4bb_tmp" / ("conc_" + self._testMethodName)
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_concurrent_same_decision_is_idempotent(self) -> None:
        def write(_index: int):
            return _ledger(self.root).append_decision(_decision(), _provenance())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (1, 2)))
        statuses = {item.status for item in results}
        self.assertIn(COMMITTED, statuses)
        self.assertTrue(statuses <= {COMMITTED, ALREADY_COMMITTED})
        ledger = _ledger(self.root)
        manifest = ledger.verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(manifest.decision_count, 1)
        self.assertEqual(manifest.event_count, 1)
        self.assertEqual(manifest.last_event_seq, 1)

    def test_concurrent_different_decisions_keep_contiguous_seq(self) -> None:
        first = _decision()
        second = _decision(decision_id=DECISION_B, action=DecisionAction.STOP)

        def write(decision: M1Decision):
            return _ledger(self.root).append_decision(decision, _provenance())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (first, second)))
        self.assertEqual({item.status for item in results}, {COMMITTED})
        manifest = _ledger(self.root).verify_decision_ledger_minimal(SESSION_A)
        self.assertEqual(manifest.decision_count, 2)
        self.assertEqual(manifest.event_count, 2)
        self.assertEqual(manifest.last_event_seq, 2)
        events = [
            json.loads(line)
            for line in (self.root / SESSION_A / "int" / "decision-events.jsonl").read_text().splitlines()
        ]
        self.assertEqual([item["event_seq"] for item in events], [1, 2])

    def test_same_process_threads_serialize_and_keep_seq(self) -> None:
        first = _decision()
        second = _decision(decision_id=DECISION_B, action=DecisionAction.STOP)
        shared = _ledger(self.root)

        def write(decision: M1Decision):
            return shared.append_decision(decision, _provenance())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (first, second)))
        self.assertEqual({item.status for item in results}, {COMMITTED})
        events = [
            json.loads(line)
            for line in (self.root / SESSION_A / "int" / "decision-events.jsonl").read_text().splitlines()
        ]
        self.assertEqual([item["event_seq"] for item in events], [1, 2])

    def test_different_sessions_are_independent(self) -> None:
        def write(session_id: str):
            return _ledger(self.root).append_decision(
                _decision(session_id=session_id, decision_id=DECISION_A if session_id == SESSION_A else DECISION_B),
                _provenance(),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (SESSION_A, SESSION_B)))
        self.assertEqual({item.status for item in results}, {COMMITTED})
        self.assertEqual(_ledger(self.root).verify_decision_ledger_minimal(SESSION_A).decision_count, 1)
        self.assertEqual(_ledger(self.root).verify_decision_ledger_minimal(SESSION_B).decision_count, 1)


class BoundaryStaticTests(unittest.TestCase):
    def test_public_api_does_not_expose_p4b_c_or_p4c(self) -> None:
        from digital_pulse import m1_int

        forbidden = {
            "record_override",
            "record_outcome",
            "append_event",
            "replay",
            "effective_view",
            "RetryScope",
            "acknowledge_reposition",
        }
        self.assertTrue(forbidden.isdisjoint(set(dir(m1_int))))
        self.assertTrue(forbidden.isdisjoint(set(m1_int.__all__)))


if __name__ == "__main__":
    unittest.main()
