"""Tests for the madObsidian command-line interface."""

import contextlib
import io
import unittest

from madobsidian.cli import main


class CliTests(unittest.TestCase):
    def test_version(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_info:
            main(["--version"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "madObsidian 0.1.0")

    def test_status(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["status"])
        self.assertEqual(result, 0)
        self.assertEqual(
            output.getvalue().strip(),
            "madObsidian 0.1.0: ready (no archive configured)",
        )

    def test_help(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_info:
            main(["--help"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("usage: madObsidian", output.getvalue())
        self.assertIn("status", output.getvalue())


if __name__ == "__main__":
    unittest.main()
