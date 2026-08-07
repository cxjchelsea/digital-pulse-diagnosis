from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from digital_pulse.m1_simulator.cli import EXIT_OK, EXIT_USAGE, EXIT_VALIDATE, EXIT_WRITE, main


class M1SimulatorCliTests(unittest.TestCase):
    def test_list_and_generate_replay_validate(self):
        self.assertEqual(main(["list"]), EXIT_OK)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = main(
                [
                    "generate",
                    "--scenario",
                    "normal_high_quality",
                    "--seed",
                    "11",
                    "--duration",
                    "0.2",
                    "--output",
                    str(root),
                    "--json",
                ]
            )
            self.assertEqual(code, EXIT_OK)
            session_dirs = [path for path in root.iterdir() if path.is_dir()]
            self.assertEqual(len(session_dirs), 1)
            session = session_dirs[0]
            self.assertEqual(main(["replay", str(session), "--json"]), EXIT_OK)
            self.assertEqual(main(["validate", str(session), "--json"]), EXIT_OK)

            code = main(
                [
                    "generate",
                    "--plan",
                    "retry_improves",
                    "--seed",
                    "11",
                    "--duration",
                    "0.2",
                    "--output",
                    str(root),
                ]
            )
            self.assertEqual(code, EXIT_OK)

            code = main(
                [
                    "generate",
                    "--scenario",
                    "raw_persistence_failure",
                    "--seed",
                    "11",
                    "--duration",
                    "0.4",
                    "--output",
                    str(root),
                ]
            )
            self.assertEqual(code, EXIT_OK)

    def test_usage_errors(self):
        self.assertEqual(main(["generate", "--output", "x"]), EXIT_USAGE)
        self.assertEqual(
            main(["generate", "--scenario", "nope", "--output", "x"]),
            EXIT_USAGE,
        )
        self.assertEqual(
            main(["generate", "--plan", "nope", "--output", "x"]),
            EXIT_USAGE,
        )
        self.assertEqual(
            main(
                [
                    "generate",
                    "--scenario",
                    "normal_high_quality",
                    "--plan",
                    "retry_improves",
                    "--output",
                    "x",
                ]
            ),
            EXIT_USAGE,
        )

    def test_existing_session_write_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = [
                "generate",
                "--scenario",
                "normal_high_quality",
                "--seed",
                "5",
                "--duration",
                "0.2",
                "--session-id",
                "fixed-session",
                "--output",
                str(root),
            ]
            self.assertEqual(main(args), EXIT_OK)
            self.assertEqual(main(args), EXIT_WRITE)

    def test_validate_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(
                [
                    "generate",
                    "--scenario",
                    "normal_high_quality",
                    "--seed",
                    "9",
                    "--duration",
                    "0.2",
                    "--output",
                    str(root),
                ]
            )
            session = next(root.iterdir())
            (session / "manifest.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(main(["validate", str(session)]), EXIT_VALIDATE)

    def test_module_entry_list(self):
        proc = subprocess.run(
            [sys.executable, "-m", "digital_pulse.m1_simulator", "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["total"], 18)


if __name__ == "__main__":
    unittest.main()
