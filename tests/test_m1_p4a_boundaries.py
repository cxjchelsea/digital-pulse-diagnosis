"""M1-P4A 边界：错误模型、oracle 隔离、无持久化、无医学语义。"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from digital_pulse.m1_contracts import QualityLabel, QualityReference
from digital_pulse.m1_int import I1PolicyConfig, I1RuleEngine, M1IntError

from _m1_p4a_helpers import SP_VERSION, early_failure_context, make_context

PKG = Path(__file__).resolve().parents[1] / "src" / "digital_pulse" / "m1_int"


class InvalidInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = I1RuleEngine()
        self.policy = I1PolicyConfig()

    def test_missing_session_id(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(make_context(session_id=""), self.policy)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_invalid_retry_count(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(make_context(retry_count=-1), self.policy)
        self.assertEqual(raised.exception.code, "invalid_retry_state")

    def test_retry_count_above_max(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(make_context(retry_count=3), self.policy)
        self.assertEqual(raised.exception.code, "invalid_retry_state")

    def test_empty_retry_scope_id(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(make_context(retry_scope_id=""), self.policy)
        self.assertEqual(raised.exception.code, "invalid_retry_state")

    def test_quality_reference_session_mismatch(self) -> None:
        context = make_context()
        mismatched = QualityFactsReplacement(context)
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(mismatched, self.policy)
        self.assertEqual(raised.exception.code, "provenance_mismatch")

    def test_missing_run_sp_version(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(make_context(run_signal_processing_version=None), self.policy)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_run_session_sp_version_mismatch(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(
                make_context(
                    run_signal_processing_version=SP_VERSION,
                    session_signal_processing_version="other-sp",
                ),
                self.policy,
            )
        self.assertEqual(raised.exception.code, "provenance_mismatch")

    def test_normal_quality_path_missing_app_run(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(
                make_context(
                    quality_label=QualityLabel.ACCEPTABLE,
                    app_run_id=None,
                    app_analysis_fingerprint=None,
                    sp_result_fingerprint=None,
                    run_signal_processing_version=None,
                    session_signal_processing_version=SP_VERSION,
                ),
                self.policy,
            )
        self.assertIn(raised.exception.code, {"provenance_mismatch", "invalid_input"})

    def test_contradictory_history_lengths(self) -> None:
        with self.assertRaises(M1IntError) as raised:
            self.engine.evaluate(
                make_context(prior_decision_ids=("m1-decision-" + "a" * 64,), prior_actions=()),
                self.policy,
            )
        self.assertEqual(raised.exception.code, "invalid_retry_state")

    def test_errors_are_not_m1_decisions(self) -> None:
        with self.assertRaises(M1IntError):
            self.engine.evaluate(early_failure_context(device_state="FAULT"), self.policy)


def QualityFactsReplacement(context):
    """替换 quality_reference.session_id 以制造溯源冲突。"""

    from dataclasses import replace

    from digital_pulse.m1_int import QualityFacts

    return replace(
        context,
        quality=QualityFacts(
            quality_label=context.quality.quality_label,
            quality_reference=QualityReference(session_id="other-session", window_id="window-0001"),
            analysis_allowed=context.quality.analysis_allowed,
        ),
    )


class BoundaryScanTests(unittest.TestCase):
    def test_oracle_isolation_imports(self) -> None:
        forbidden_modules = {
            "digital_pulse.m1_simulator",
            "digital_pulse.m1_simulator.scenarios",
        }
        forbidden_names = {
            "expected_int_action",
            "expected_quality_label",
            "ScenarioDefinition",
            "expected.json",
            "scenario.json",
        }
        for path in PKG.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            any(alias.name == item or alias.name.startswith(item + ".") for item in forbidden_modules),
                            path.name,
                        )
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(
                        any(module == item or module.startswith(item + ".") for item in forbidden_modules),
                        path.name,
                    )
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, path.name)
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_names, path.name)

    def test_no_persistence_writes_in_package(self) -> None:
        forbidden_snippets = (
            "decisions.jsonl",
            "decision-events.jsonl",
            "int/manifest.json",
            "int/reports/",
            "os.fsync",
            ".write_text(",
            ".write_bytes(",
            'open(',
        )
        for path in PKG.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                if snippet == "open(":
                    tree = ast.parse(source, filename=str(path))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                            self.fail(f"{path.name} 含 open()，P4A 禁止持久化")
                    continue
                self.assertNotIn(snippet, source, path.name)

    def test_no_medical_runtime_semantics(self) -> None:
        forbidden = ("诊断", "疾病", "证型", "治疗", "临床", "diagnosis", "disease", "syndrome", "treatment")
        for path in PKG.glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for term in forbidden:
                self.assertNotIn(term.lower(), source, f"{path.name}:{term}")


if __name__ == "__main__":
    unittest.main()
