"""Focused tests for per-file rollback, recovery, and CLI reporting."""

from __future__ import annotations

import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from madobsidian.cli import main
from madobsidian.exclusions import exclusion_path, load_exclusions
from madobsidian.importer import import_vault


class ImportFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.vault = self.base / "vault"
        (self.vault / "Incoming").mkdir(parents=True)
        self.addCleanup(self.temporary.cleanup)

    def add_file(self, name: str, contents: bytes = b"media bytes") -> Path:
        source = self.vault / "Incoming" / name
        source.write_bytes(contents)
        note = self.vault / "note.md"
        previous = note.read_text(encoding="utf-8") if note.exists() else ""
        note.write_text(previous + f"\n![[Incoming/{name}]]", encoding="utf-8")
        return source

    def previews_fail_for(self, failed_names: set[str]):
        def image_preview(original: Path, *_args, **_kwargs):
            if original.name in failed_names:
                raise OSError(f"preview failed for {original.name}")
            return None

        return (
            mock.patch("madobsidian.importer.generate_preview", side_effect=image_preview),
            mock.patch("madobsidian.importer.generate_video_preview", return_value=None),
        )

    def test_preview_failure_restores_and_hash_verifies_original(self) -> None:
        source = self.add_file("broken.jpg", b"original bytes")
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.previews_fail_for({source.name})[0], self.previews_fail_for({source.name})[1]:
            batch = import_vault(self.vault)

        self.assertEqual(source.read_bytes(), b"original bytes")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(len(batch.failures), 1)
        self.assertFalse(batch.failures[0]["serious"])
        self.assertFalse(list((self.vault / "objects").glob("[0-9a-f]*")))
        self.assertFalse(list((self.vault / "objects").glob(".incomplete-*")))

    def test_failed_first_file_does_not_prevent_next_file(self) -> None:
        failed = self.add_file("a-fails.jpg")
        successful = self.add_file("b-succeeds.jpg")
        first, second = self.previews_fail_for({failed.name})
        with first, second:
            batch = import_vault(self.vault)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["source"], "Incoming/b-succeeds.jpg")
        self.assertEqual(len(batch.failures), 1)
        self.assertTrue(failed.is_file())
        self.assertFalse(successful.exists())

    def test_failed_middle_file_does_not_prevent_later_file(self) -> None:
        first = self.add_file("a.jpg")
        failed = self.add_file("b.jpg")
        last = self.add_file("c.jpg")
        one, two = self.previews_fail_for({failed.name})
        with one, two:
            batch = import_vault(self.vault)
        self.assertEqual([row["source"] for row in batch], ["Incoming/a.jpg", "Incoming/c.jpg"])
        self.assertEqual(len(batch.failures), 1)
        self.assertFalse(first.exists())
        self.assertTrue(failed.exists())
        self.assertFalse(last.exists())

    def test_failed_last_file_preserves_it_after_prior_successes(self) -> None:
        first = self.add_file("a.jpg")
        second = self.add_file("b.jpg")
        failed = self.add_file("c.jpg")
        one, two = self.previews_fail_for({failed.name})
        with one, two:
            batch = import_vault(self.vault)
        self.assertEqual([row["source"] for row in batch], ["Incoming/a.jpg", "Incoming/b.jpg"])
        self.assertEqual(len(batch.failures), 1)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(failed.exists())

    def test_rollback_failure_keeps_stage_journal_and_is_recoverable(self) -> None:
        source = self.add_file("needs-recovery.jpg", b"recover me")
        self.add_file("continues.jpg", b"unrelated file")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        first, second = self.previews_fail_for({source.name})
        with first, second, mock.patch(
            "madobsidian.importer._restore_moved_staging_original",
            side_effect=OSError("simulated rollback failure"),
        ):
            batch = import_vault(self.vault)
        self.assertEqual(len(batch.failures), 1)
        self.assertTrue(batch.failures[0]["serious"])
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["source"], "Incoming/continues.jpg")
        self.assertFalse((self.vault / "Incoming" / "continues.jpg").exists())
        self.assertFalse(source.exists())
        stages = list((self.vault / "objects").glob(".incomplete-*"))
        self.assertEqual(len(stages), 1)
        self.assertFalse((self.vault.parent / "exclusions.ini").exists())
        staged_original = stages[0] / "shared" / source.name
        self.assertEqual(hashlib.sha256(staged_original.read_bytes()).hexdigest(), digest)
        self.assertTrue((stages[0] / "transaction.json").is_file())

        recovered, _ = __import__("madobsidian.importer", fromlist=["recover_transactions"]).recover_transactions(self.vault)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertFalse(list((self.vault / "objects").glob(".incomplete-*")))

    def test_unchanged_failed_file_is_ignored_until_exclusion_is_removed(self) -> None:
        source = self.add_file("same.jpg", b"failed content")
        first, second = self.previews_fail_for({source.name})
        with first, second:
            failed = import_vault(self.vault)

        self.assertEqual(len(failed.failures), 1)
        self.assertTrue(source.is_file())
        exclusions_file = exclusion_path(None, self.vault)
        self.assertEqual(set(load_exclusions(exclusions_file).values()), {"same.jpg"})

        with mock.patch("madobsidian.importer._sha256", side_effect=AssertionError("excluded file was hashed")), \
                mock.patch("madobsidian.importer.generate_preview", side_effect=AssertionError("preview attempted")):
            ignored = import_vault(self.vault)
        self.assertEqual(ignored.ignored, [{"source": "same.jpg", "reason": "listed in exclusions.ini"}])
        self.assertTrue(source.is_file())

        exclusions_file.write_text("[ignore]\n", encoding="utf-8")
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
                "madobsidian.importer.generate_video_preview", return_value=None):
            retried = import_vault(self.vault)

        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["source"], "Incoming/same.jpg")
        self.assertFalse(source.exists())
        self.assertFalse(load_exclusions(exclusions_file))

    def test_unsupported_file_remains_in_incoming(self) -> None:
        unsupported = self.add_file("notes.txt", b"not media")
        batch = import_vault(self.vault)
        self.assertEqual(len(batch.skipped), 1)
        self.assertTrue(unsupported.exists())

    def test_cli_partial_failure_is_nonzero_and_reports_counts(self) -> None:
        failed = self.add_file("a-fail.jpg")
        self.add_file("b-pass.jpg")
        one, two = self.previews_fail_for({failed.name})
        output, errors = io.StringIO(), io.StringIO()
        with one, two, contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["import", str(self.vault)])
        self.assertEqual(result, 1)
        self.assertIn("Imported 1 file(s); skipped 0; ignored 0; failed 1.", output.getvalue())
        self.assertIn("Failed: Incoming/a-fail.jpg", errors.getvalue())

        ignored_output = io.StringIO()
        with contextlib.redirect_stdout(ignored_output):
            ignored_result = main(["import", str(self.vault)])
        self.assertEqual(ignored_result, 0)
        self.assertIn("Imported 0 file(s); skipped 0; ignored 1; failed 0.", ignored_output.getvalue())
        self.assertIn("Ignored: a-fail.jpg (listed in exclusions.ini)", ignored_output.getvalue())

    def test_cli_successful_batch_returns_zero(self) -> None:
        self.add_file("good.jpg")
        output = io.StringIO()
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer.generate_video_preview", return_value=None
        ), contextlib.redirect_stdout(output):
            result = main(["import", str(self.vault)])
        self.assertEqual(result, 0)
        self.assertIn("Imported 1 file(s); skipped 0; ignored 0; failed 0.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
