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

    def test_obsolete_failed_imports_command_is_not_available(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_info:
            main(["failed-imports", "list"])
        self.assertEqual(exit_info.exception.code, 2)

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

    def test_legacy_dry_run_is_rejected_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "item.txt"
            source.write_text("must remain", encoding="utf-8")
            archive = root / "archive"
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                result = main(["import", str(source), str(archive), "--dry-run"])
            self.assertEqual(result, 1)
            self.assertIn("--dry-run is only supported for vault imports", output.getvalue())
            self.assertTrue(source.exists())
            self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
