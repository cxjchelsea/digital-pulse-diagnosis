"""P3D acceptance harness helpers: npm argv construction + Vitest parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_m1_p3d_acceptance.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("generate_m1_p3d_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class M1P3DAcceptanceHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = _load_harness()

    def test_npm_command_uses_npm_cmd_on_windows(self) -> None:
        command = self.harness._npm_command("test", "--", "--run")
        if self.harness.os.name == "nt":
            self.assertEqual(command[0], "npm.cmd")
        else:
            self.assertEqual(command[0], "npm")
        self.assertEqual(command[1:], ["test", "--", "--run"])

    def test_npm_command_preserves_ci_and_build_args(self) -> None:
        self.assertEqual(self.harness._npm_command("ci")[1:], ["ci"])
        self.assertEqual(self.harness._npm_command("run", "build")[1:], ["run", "build"])

    def test_vitest_prefers_tests_over_test_files(self) -> None:
        output = "Test Files  3 passed (3)\nTests       17 passed (17)\n"
        passed_count, failed_count = self.harness._parse_vitest_counts(output)
        self.assertEqual(passed_count, 17)
        self.assertIsNone(failed_count)

    def test_vitest_single_line_case_count(self) -> None:
        output = "Test Files  1 passed\nTests       8 passed\n"
        passed_count, _failed = self.harness._parse_vitest_counts(output)
        self.assertEqual(passed_count, 8)

    def test_vitest_falls_back_to_test_files(self) -> None:
        output = "Test Files  3 passed\n"
        passed_count, _failed = self.harness._parse_vitest_counts(output)
        self.assertEqual(passed_count, 3)

    def test_vitest_failed_and_passed_on_same_line(self) -> None:
        output = "Tests  1 failed | 16 passed\n"
        passed_count, failed_count = self.harness._parse_vitest_counts(output)
        self.assertEqual(passed_count, 16)
        self.assertEqual(failed_count, 1)

    def test_vitest_ansi_colored_ci_output(self) -> None:
        output = (
            "\x1b[2m Test Files \x1b[22m \x1b[1m\x1b[32m2 passed\x1b[39m\x1b[22m\x1b[90m (2)\x1b[39m\n"
            "\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[32m11 passed\x1b[39m\x1b[22m\x1b[90m (11)\x1b[39m\n"
        )
        passed_count, failed_count = self.harness._parse_vitest_counts(output)
        self.assertEqual(passed_count, 11)
        self.assertIsNone(failed_count)


if __name__ == "__main__":
    unittest.main()
