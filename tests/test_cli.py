"""Tests for the madObsidian command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

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

    def test_import_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "item.txt"
            source.write_text("content", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["import", str(source), str(root / "archive")])
            self.assertEqual(result, 0)
            self.assertIn("Imported 1 file(s).", output.getvalue())


if __name__ == "__main__":
    unittest.main()
