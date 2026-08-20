"""P4B-A 纯合同层测试：事件身份、覆盖矩阵、manifest 校验。不含磁盘 IO。"""

from __future__ import annotations

import ast
from dataclasses import replace
import inspect
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
from digital_pulse.m1_int import M1IntError
from digital_pulse.m1_int.ledger_models import (
    EMPTY_LEDGER_DIGEST,
    FROZEN_EVENT_TYPES,
    FROZEN_OUTCOMES,
    FROZEN_RESOLUTIONS,
    LEDGER_MANIFEST_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    IntLedgerEvent,
    IntLedgerManifest,
    build_int_ledger_event,
    canonical_event_bytes,
    canonical_event_payload,
    event_fingerprint,
    require_frozen_outcome,
    require_frozen_resolution,
    require_machine_decision_record,
    validate_int_ledger_event,
    validate_int_ledger_manifest,
)
from digital_pulse.m1_int.override_safety import (
    OverrideClassification,
    classify_override,
    is_override_allowed,
)

ROOT = Path(__file__).resolve().parents[1]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
SESSION_ID = "session-p4b-a-001"
DECISION_ID = "m1-decision-" + ("ab" * 32)
OCCURRED_AT = "2026-01-01T00:00:00Z"
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_SOFTWARE_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CONFIG_DIGEST = "cd" * 32
DECISIONS_DIGEST = "11" * 32
EVENTS_DIGEST = "22" * 32


def _machine_decision(*, operator_override=None, outcome=None) -> M1Decision:
    """构造可被 P4B 接受或拒绝的机器决策夹具。"""

    return M1Decision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        decided_at_utc=OCCURRED_AT,
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=SESSION_ID, window_id="window-0001"),
        action=DecisionAction.ACCEPT,
        reason_codes=("quality_acceptable",),
        rule_version="i1-pre-0.1.0",
        input_versions=DecisionInputVersions(
            signal_processing_version="0.4.0-p2d",
            decision_rule_version="i1-pre-0.1.0",
            configuration_digest=CONFIG_DIGEST,
        ),
        retry_count=0,
        max_retry_count=2,
        operator_override=operator_override,
        outcome=outcome,
        parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
    )


def _recorded_event(**overrides) -> IntLedgerEvent:
    """构造一条已校验的 decision_recorded 事件。"""

    fields = {
        "event_seq": 1,
        "event_type": "decision_recorded",
        "session_id": SESSION_ID,
        "decision_id": DECISION_ID,
        "occurred_at_utc": OCCURRED_AT,
        "software_commit_sha": SOFTWARE_SHA,
        "rule_version": "i1-pre-0.1.0",
        "configuration_digest": CONFIG_DIGEST,
    }
    fields.update(overrides)
    return build_int_ledger_event(**fields)


class EventTypeContractTests(unittest.TestCase):
    def test_all_frozen_event_types_are_accepted(self) -> None:
        builders = {
            "decision_recorded": lambda: _recorded_event(),
            "operator_override": lambda: build_int_ledger_event(
                event_seq=2,
                event_type="operator_override",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                requested_action="stop",
                operator_id="op-001",
                note="upgrade-to-stop",
            ),
            "action_applied": lambda: build_int_ledger_event(
                event_seq=3,
                event_type="action_applied",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
            ),
            "action_rejected_by_safety": lambda: build_int_ledger_event(
                event_seq=4,
                event_type="action_rejected_by_safety",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                requested_action="accept",
                operator_id="op-001",
            ),
            "decision_completed": lambda: build_int_ledger_event(
                event_seq=5,
                event_type="decision_completed",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
            ),
            "awaiting_operator": lambda: build_int_ledger_event(
                event_seq=6,
                event_type="awaiting_operator",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
            ),
            "reposition_acknowledged": lambda: build_int_ledger_event(
                event_seq=7,
                event_type="reposition_acknowledged",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                operator_id="op-001",
                prior_scope_id="m1-retry-scope-" + ("aa" * 32),
                new_session_id="session-p4b-a-002",
            ),
            "manual_review_resolved": lambda: build_int_ledger_event(
                event_seq=8,
                event_type="manual_review_resolved",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                operator_id="op-001",
                resolution="remain_awaiting",
            ),
            "retry_scope_started": lambda: build_int_ledger_event(
                event_seq=9,
                event_type="retry_scope_started",
                session_id=SESSION_ID,
                occurred_at_utc=OCCURRED_AT,
                retry_scope_id="m1-retry-scope-" + ("bb" * 32),
            ),
            "retry_scope_closed": lambda: build_int_ledger_event(
                event_seq=10,
                event_type="retry_scope_closed",
                session_id=SESSION_ID,
                occurred_at_utc=OCCURRED_AT,
                retry_scope_id="m1-retry-scope-" + ("bb" * 32),
            ),
            "retry_attempt_linked": lambda: build_int_ledger_event(
                event_seq=11,
                event_type="retry_attempt_linked",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                retry_scope_id="m1-retry-scope-" + ("bb" * 32),
                linked_session_id="session-p4b-a-003",
            ),
        }
        self.assertEqual(set(builders), FROZEN_EVENT_TYPES)
        for event_type, builder in builders.items():
            with self.subTest(event_type=event_type):
                event = builder()
                self.assertEqual(event.event_type, event_type)
                self.assertTrue(event.event_id.startswith("m1-int-event-"))

    def test_unknown_event_type_is_rejected(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            build_int_ledger_event(
                event_seq=1,
                event_type="override_requested",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
            )
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_typo_and_case_variants_are_rejected(self) -> None:
        for event_type in ("OPERATOR_OVERRIDE", "operator_override ", "Decision_Recorded"):
            with self.subTest(event_type=event_type):
                with self.assertRaises(M1IntError) as raised:
                    build_int_ledger_event(
                        event_seq=1,
                        event_type=event_type,
                        session_id=SESSION_ID,
                        decision_id=DECISION_ID,
                        occurred_at_utc=OCCURRED_AT,
                    )
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_forbidden_fields_on_wrong_event_types_are_rejected(self) -> None:
        attacks = (
            {
                "event_type": "action_applied",
                "decision_id": DECISION_ID,
                "operator_id": "op-sneak",
            },
            {
                "event_type": "action_applied",
                "decision_id": DECISION_ID,
                "retry_scope_id": "m1-retry-scope-" + ("aa" * 32),
            },
            {
                "event_type": "decision_recorded",
                "decision_id": DECISION_ID,
                "software_commit_sha": SOFTWARE_SHA,
                "rule_version": "i1-pre-0.1.0",
                "configuration_digest": CONFIG_DIGEST,
                "operator_id": "op-sneak",
            },
            {
                "event_type": "retry_scope_started",
                "retry_scope_id": "m1-retry-scope-" + ("aa" * 32),
                "decision_id": DECISION_ID,
            },
            {
                "event_type": "awaiting_operator",
                "decision_id": DECISION_ID,
                "note": "not-allowed",
            },
        )
        for kwargs in attacks:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(M1IntError) as raised:
                    build_int_ledger_event(
                        event_seq=1,
                        session_id=SESSION_ID,
                        occurred_at_utc=OCCURRED_AT,
                        **kwargs,
                    )
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_zero_and_negative_event_seq_are_rejected(self) -> None:
        for event_seq in (0, -1):
            with self.subTest(event_seq=event_seq):
                with self.assertRaises(M1IntError) as raised:
                    build_int_ledger_event(
                        event_seq=event_seq,
                        event_type="action_applied",
                        session_id=SESSION_ID,
                        decision_id=DECISION_ID,
                        occurred_at_utc=OCCURRED_AT,
                    )
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_operator_override_rejects_blank_note(self) -> None:
        for note in ("", "   "):
            with self.subTest(note=repr(note)):
                with self.assertRaises(M1IntError) as raised:
                    build_int_ledger_event(
                        event_seq=2,
                        event_type="operator_override",
                        session_id=SESSION_ID,
                        decision_id=DECISION_ID,
                        occurred_at_utc=OCCURRED_AT,
                        requested_action="stop",
                        operator_id="op-001",
                        note=note,
                    )
                self.assertEqual(raised.exception.code, "invalid_input")

    def test_mismatched_event_outcome_combinations_are_rejected(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            build_int_ledger_event(
                event_seq=1,
                event_type="decision_completed",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                outcome="applied",
            )
        self.assertEqual(raised.exception.code, "invalid_input")


class EventIdentityTests(unittest.TestCase):
    def test_event_id_is_deterministic(self) -> None:
        first = _recorded_event()
        second = _recorded_event()
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(event_fingerprint(first), event_fingerprint(second))
        self.assertEqual(canonical_event_bytes(first), canonical_event_bytes(second))

    def test_event_id_is_excluded_from_identity_payload(self) -> None:
        event = _recorded_event()
        payload = canonical_event_payload(event)
        self.assertNotIn("event_id", payload)
        self.assertEqual(event.event_id, f"m1-int-event-{event_fingerprint(event)}")

    def test_occurred_at_utc_does_not_change_event_id(self) -> None:
        first = _recorded_event(occurred_at_utc="2026-01-01T00:00:00Z")
        second = _recorded_event(occurred_at_utc="2026-08-19T15:00:00Z")
        self.assertEqual(first.event_id, second.event_id)

    def test_software_sha_does_not_change_event_id(self) -> None:
        first = _recorded_event(software_commit_sha=SOFTWARE_SHA)
        second = _recorded_event(software_commit_sha=OTHER_SOFTWARE_SHA)
        self.assertEqual(first.event_id, second.event_id)

    def test_canonical_bytes_ignore_dict_insertion_order(self) -> None:
        event = _recorded_event()
        payload = canonical_event_payload(event)
        reversed_keys = {key: payload[key] for key in reversed(list(payload))}
        from digital_pulse.m1_int.models import dumps_canonical

        self.assertEqual(dumps_canonical(payload), dumps_canonical(reversed_keys))
        self.assertEqual(canonical_event_bytes(event), dumps_canonical(payload).encode("utf-8"))

    def test_identity_payload_excludes_paths_and_clock(self) -> None:
        event = _recorded_event()
        payload = canonical_event_payload(event)
        serialized = canonical_event_bytes(event).decode("utf-8")
        self.assertNotIn("occurred_at_utc", payload)
        self.assertNotIn("software_commit_sha", payload)
        self.assertNotIn("path", payload)
        self.assertNotIn("\\\\", serialized)
        self.assertNotIn("/tmp/", serialized)
        self.assertNotIn("sessions/", serialized)

    def test_distinct_semantic_fields_do_not_collide(self) -> None:
        first = _recorded_event(event_seq=1)
        second = _recorded_event(event_seq=2)
        third = build_int_ledger_event(
            event_seq=1,
            event_type="operator_override",
            session_id=SESSION_ID,
            decision_id=DECISION_ID,
            occurred_at_utc=OCCURRED_AT,
            requested_action="stop",
            operator_id="op-001",
            note="upgrade-to-stop",
        )
        identities = {first.event_id, second.event_id, third.event_id}
        self.assertEqual(len(identities), 3)

    def test_override_semantic_mutations_change_event_id(self) -> None:
        base = build_int_ledger_event(
            event_seq=2,
            event_type="operator_override",
            session_id=SESSION_ID,
            decision_id=DECISION_ID,
            occurred_at_utc=OCCURRED_AT,
            requested_action="stop",
            operator_id="op-001",
            note="n1",
        )
        variants = [
            build_int_ledger_event(
                event_seq=2,
                event_type="operator_override",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                requested_action="manual_review",
                operator_id="op-001",
                note="n1",
            ),
            build_int_ledger_event(
                event_seq=2,
                event_type="operator_override",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                requested_action="stop",
                operator_id="op-002",
                note="n1",
            ),
            build_int_ledger_event(
                event_seq=2,
                event_type="operator_override",
                session_id=SESSION_ID,
                decision_id=DECISION_ID,
                occurred_at_utc=OCCURRED_AT,
                requested_action="stop",
                operator_id="op-001",
                note="n2",
            ),
        ]
        self.assertEqual(len({base.event_id, *[item.event_id for item in variants]}), 4)

    def test_forged_event_id_is_rejected_by_validator(self) -> None:
        event = _recorded_event()
        forged = replace(event, event_id="m1-int-event-" + ("0" * 64))
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_event(forged)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_wrong_prefix_event_id_is_rejected(self) -> None:
        event = _recorded_event()
        forged = replace(event, event_id="m1-decision-" + event.event_id.split("-", 3)[-1])
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_event(forged)
        self.assertEqual(raised.exception.code, "invalid_input")


class ManifestContractTests(unittest.TestCase):
    def _empty_manifest(self) -> IntLedgerManifest:
        return IntLedgerManifest(
            schema_version=LEDGER_MANIFEST_SCHEMA_VERSION,
            session_id=SESSION_ID,
            decision_rule_version="i1-pre-0.1.0",
            configuration_digest=CONFIG_DIGEST,
            software_commit_sha=SOFTWARE_SHA,
            decisions_sha256=EMPTY_LEDGER_DIGEST,
            events_sha256=EMPTY_LEDGER_DIGEST,
            decision_count=0,
            event_count=0,
            last_event_seq=0,
            current_decision_id=None,
        )

    def test_valid_empty_manifest(self) -> None:
        validate_int_ledger_manifest(self._empty_manifest())

    def test_valid_populated_manifest(self) -> None:
        validate_int_ledger_manifest(
            IntLedgerManifest(
                schema_version=LEDGER_MANIFEST_SCHEMA_VERSION,
                session_id=SESSION_ID,
                decision_rule_version="i1-pre-0.1.0",
                configuration_digest=CONFIG_DIGEST,
                software_commit_sha=SOFTWARE_SHA,
                decisions_sha256=DECISIONS_DIGEST,
                events_sha256=EVENTS_DIGEST,
                decision_count=1,
                event_count=2,
                last_event_seq=2,
                current_decision_id=DECISION_ID,
            )
        )

    def test_invalid_counts_fail_closed(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_manifest(replace(self._empty_manifest(), decision_count=-1))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_empty_ledger_requires_zero_last_event_seq(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_manifest(replace(self._empty_manifest(), last_event_seq=1))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_wrong_schema_is_version_mismatch(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_manifest(replace(self._empty_manifest(), schema_version="1.0.0"))
        self.assertEqual(raised.exception.code, "version_mismatch")

    def test_malformed_digest_is_rejected(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_manifest(
                replace(self._empty_manifest(), configuration_digest="not-a-digest")
            )
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_wrong_session_binding_is_rejected(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            validate_int_ledger_manifest(replace(self._empty_manifest(), session_id=""))
        self.assertEqual(raised.exception.code, "invalid_input")


class OverrideSafetyTests(unittest.TestCase):
    def test_allowed_transitions(self) -> None:
        allowed = {
            ("accept", "manual_review"),
            ("accept", "stop"),
            ("retry_same_position", "stop"),
            ("retry_same_position", "manual_review"),
            ("reposition", "stop"),
            ("reposition", "manual_review"),
            ("manual_review", "stop"),
            ("stop", "abort_and_release"),
        }
        for machine_action, requested_action in allowed:
            with self.subTest(machine_action=machine_action, requested_action=requested_action):
                self.assertTrue(is_override_allowed(machine_action, requested_action))
                self.assertEqual(
                    classify_override(machine_action, requested_action),
                    OverrideClassification.ALLOWED,
                )

    def test_same_action_is_not_an_override(self) -> None:
        classification = classify_override("stop", "stop")
        self.assertEqual(classification, OverrideClassification.IDEMPOTENT_SAME_ACTION)
        self.assertFalse(is_override_allowed("stop", "stop"))

    def test_forbidden_downgrades_fail_closed(self) -> None:
        forbidden = (
            ("abort_and_release", "accept"),
            ("abort_and_release", "retry_same_position"),
            ("abort_and_release", "reposition"),
            ("abort_and_release", "manual_review"),
            ("abort_and_release", "stop"),
            ("stop", "accept"),
            ("stop", "retry_same_position"),
            ("stop", "reposition"),
            ("stop", "manual_review"),
            ("manual_review", "accept"),
            ("accept", "abort_and_release"),
            ("retry_same_position", "abort_and_release"),
            ("reposition", "abort_and_release"),
            ("manual_review", "abort_and_release"),
        )
        for machine_action, requested_action in forbidden:
            with self.subTest(machine_action=machine_action, requested_action=requested_action):
                self.assertFalse(is_override_allowed(machine_action, requested_action))
                self.assertEqual(
                    classify_override(machine_action, requested_action),
                    OverrideClassification.REJECTED_BY_SAFETY,
                )

    def test_full_six_by_six_matrix_is_closed(self) -> None:
        actions = (
            "accept",
            "retry_same_position",
            "reposition",
            "manual_review",
            "stop",
            "abort_and_release",
        )
        allowed = {
            ("accept", "manual_review"),
            ("accept", "stop"),
            ("retry_same_position", "stop"),
            ("retry_same_position", "manual_review"),
            ("reposition", "stop"),
            ("reposition", "manual_review"),
            ("manual_review", "stop"),
            ("stop", "abort_and_release"),
        }
        for machine_action in actions:
            for requested_action in actions:
                classification = classify_override(machine_action, requested_action)
                with self.subTest(machine_action=machine_action, requested_action=requested_action):
                    if machine_action == requested_action:
                        self.assertEqual(
                            classification, OverrideClassification.IDEMPOTENT_SAME_ACTION
                        )
                        self.assertFalse(is_override_allowed(machine_action, requested_action))
                    elif (machine_action, requested_action) in allowed:
                        self.assertEqual(classification, OverrideClassification.ALLOWED)
                        self.assertTrue(is_override_allowed(machine_action, requested_action))
                    else:
                        self.assertEqual(
                            classification, OverrideClassification.REJECTED_BY_SAFETY
                        )
                        self.assertFalse(is_override_allowed(machine_action, requested_action))


class ResolutionAndOutcomeTests(unittest.TestCase):
    def test_only_frozen_resolutions_are_accepted(self) -> None:
        for resolution in FROZEN_RESOLUTIONS:
            require_frozen_resolution(resolution)
        with self.assertRaises(M1IntError) as raised:
            require_frozen_resolution("accept_current_quality")
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_only_frozen_outcomes_are_accepted(self) -> None:
        for outcome in FROZEN_OUTCOMES:
            require_frozen_outcome(outcome)
        with self.assertRaises(M1IntError) as raised:
            require_frozen_outcome("retried")
        self.assertEqual(raised.exception.code, "invalid_input")


class DecisionImmutabilityTests(unittest.TestCase):
    def test_machine_decision_with_override_is_rejected(self) -> None:
        decision = _machine_decision(
            operator_override=OperatorOverride(operator_id="op-001", note="patched")
        )
        with self.assertRaises(M1IntError) as raised:
            require_machine_decision_record(decision)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_machine_decision_with_outcome_is_rejected(self) -> None:
        decision = _machine_decision(outcome="applied")
        with self.assertRaises(M1IntError) as raised:
            require_machine_decision_record(decision)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_pristine_machine_decision_is_accepted(self) -> None:
        require_machine_decision_record(_machine_decision())


class P4CBoundaryTests(unittest.TestCase):
    def test_p4b_a_has_no_retryscope_or_orchestration_symbols(self) -> None:
        from digital_pulse.m1_int import ledger_models, override_safety

        forbidden_names = {
            "RetryScope",
            "RetryScopeState",
            "acknowledge_reposition",
            "consume_retry_budget",
            "schedule_next_attempt",
            "reconstruct_retry_scope",
        }
        for module in (ledger_models, override_safety):
            exported = set(dir(module))
            self.assertTrue(forbidden_names.isdisjoint(exported))
            source = inspect.getsource(module)
            self.assertNotIn("retry_count + 1", source)
            self.assertNotIn("retry_count +=", source)

    def test_scope_events_are_storage_contracts_only(self) -> None:
        event = build_int_ledger_event(
            event_seq=1,
            event_type="retry_scope_started",
            session_id=SESSION_ID,
            occurred_at_utc=OCCURRED_AT,
            retry_scope_id="m1-retry-scope-" + ("cc" * 32),
        )
        self.assertEqual(event.retry_scope_id, "m1-retry-scope-" + ("cc" * 32))
        self.assertFalse(hasattr(event, "retry_count"))


class NoPersistenceStaticTests(unittest.TestCase):
    def test_p4b_a_production_files_have_no_io(self) -> None:
        forbidden_attrs = {
            "write_text",
            "write_bytes",
            "fsync",
            "fdatasync",
            "replace",
            "rename",
        }
        forbidden_modules = {"tempfile", "fcntl", "msvcrt", "shutil"}
        for relative_name in ("ledger_models.py", "override_safety.py"):
            path = INT_PKG / relative_name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".", 1)[0], forbidden_modules)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotIn(module.split(".", 1)[0], forbidden_modules)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "open")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden_attrs)
            self.assertNotIn("decisions.jsonl", source)
            self.assertNotIn("decision-events.jsonl", source)
            self.assertNotIn(".pending-commit.json", source)


if __name__ == "__main__":
    unittest.main()
