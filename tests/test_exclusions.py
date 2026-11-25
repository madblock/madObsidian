"""Focused tests for persistent filename exclusions."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from madobsidian.config import AppConfig
from madobsidian.exclusions import (
    ExclusionsError,
    append_exclusion,
    exclusion_path,
    filename_key,
    load_exclusions,
)
from madobsidian.importer import ImportError, import_vault


def _append_exclusion_worker(path_text: str, filename: str, barrier, results) -> None:
    try:
        barrier.wait(timeout=15)
        append_exclusion(Path(path_text), filename)
        results.put(("ok", filename))
    except BaseException as error:
        results.put(("error", repr(error)))


class ExclusionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.vault = self.base / "vault"
        (self.vault / "Incoming").mkdir(parents=True)
        self.config_file = self.base / "settings" / "config.ini"
        self.config_file.parent.mkdir()
        self.config_file.write_text("[general]\n", encoding="utf-8")
        self.config = AppConfig(config_path=self.config_file)
        self.exclusions_file = exclusion_path(self.vault)

    def add_referenced_file(self, filename: str, contents: bytes = b"file contents") -> Path:
        source = self.vault / "Incoming" / filename
        source.write_bytes(contents)
        (self.vault / "note.md").write_text(f"![[Incoming/{filename}]]", encoding="utf-8")
        return source

    def test_pre_hashing_failure_preserves_source_adds_exclusion_and_ignores_rerun(self) -> None:
        filename = "unreadable-before-hash.jpg"
        original_bytes = b"intact pre-hash bytes"
        source = self.add_referenced_file(filename, original_bytes)
        note = self.vault / "note.md"
        original_note = note.read_text(encoding="utf-8")
        expected_key = filename_key(filename)

        with mock.patch("madobsidian.importer._sha256", side_effect=OSError("simulated read error")):
            first = import_vault(self.vault, config=self.config)

        self.assertEqual(first, [])
        self.assertEqual(len(first.failures), 1)
        self.assertFalse(first.failures[0]["serious"])
        self.assertIn("simulated read error", first.failures[0]["reason"])
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(note.read_text(encoding="utf-8"), original_note)
        self.assertEqual(load_exclusions(self.exclusions_file), {expected_key: filename})
        objects_dir = self.vault / "objects"
        self.assertFalse(objects_dir.exists() and any(objects_dir.iterdir()))

        with mock.patch("madobsidian.importer._sha256", side_effect=AssertionError("excluded file was hashed")), \
                mock.patch("madobsidian.importer.generate_preview", side_effect=AssertionError("preview attempted")), \
                mock.patch("madobsidian.importer.generate_video_preview", side_effect=AssertionError("video preview attempted")):
            rerun = import_vault(self.vault, config=self.config)

        self.assertEqual(rerun, [])
        self.assertEqual(rerun.failures, [])
        self.assertEqual(rerun.ignored, [{"source": filename, "reason": "listed in madobsidian_exclusions.ini"}])
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(note.read_text(encoding="utf-8"), original_note)
        self.assertFalse(objects_dir.exists() and any(objects_dir.iterdir()))

    def test_pre_transaction_failure_with_intact_source_adds_exclusion(self) -> None:
        filename = "planning-fail.jpg"
        source = self.add_referenced_file(filename, b"intact original")
        expected_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()
        
        with mock.patch("madobsidian.importer._markdown_plan", side_effect=OSError("planning failed")):
            result = import_vault(self.vault, config=self.config)
            
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.failures[0]["serious"])
        self.assertIn("planning failed", result.failures[0]["reason"])
        self.assertEqual(load_exclusions(self.exclusions_file), {expected_key: filename})
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), b"intact original")

    def test_failed_import_writes_expected_entry_after_verified_rollback(self) -> None:
        filename = "failed.jpg"
        source = self.add_referenced_file(filename, b"verified original")
        expected_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()
        with mock.patch("madobsidian.importer.generate_preview", side_effect=OSError("preview failed")):
            result = import_vault(self.vault, config=self.config)

        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), b"verified original")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(load_exclusions(self.exclusions_file), {expected_key: filename})
        self.assertIn(f'"path": "{filename}"', self.exclusions_file.read_text(encoding="utf-8"))

    def test_excluded_file_is_skipped_without_content_hash_or_preview_work(self) -> None:
        filename = "already-excluded.jpg"
        source = self.add_referenced_file(filename)
        append_exclusion(self.exclusions_file, filename)
        with mock.patch("madobsidian.importer._sha256", side_effect=AssertionError("content hash attempted")), \
                mock.patch("madobsidian.importer.generate_preview", side_effect=AssertionError("preview attempted")), \
                mock.patch("madobsidian.importer.generate_video_preview", side_effect=AssertionError("video preview attempted")):
            result = import_vault(self.vault, config=self.config)
        self.assertEqual(result, [])
        self.assertEqual(result.ignored, [{"source": filename, "reason": "listed in madobsidian_exclusions.ini"}])
        self.assertTrue(source.is_file())

    def test_manually_removing_exclusion_reenables_retry_without_stale_entry(self) -> None:
        filename = "retry.jpg"
        source = self.add_referenced_file(filename)
        append_exclusion(self.exclusions_file, filename)
        key = filename_key(filename)
        self.exclusions_file.write_text("[ignore]\n", encoding="utf-8")
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer.generate_video_preview", return_value=None
        ):
            result = import_vault(self.vault, config=self.config)
        self.assertEqual(len(result), 1)
        self.assertFalse(source.exists())
        self.assertNotIn(key, load_exclusions(self.exclusions_file))

    def test_failure_after_publication_does_not_add_exclusion(self) -> None:
        filename = "published.jpg"
        source = self.add_referenced_file(filename)
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer._atomic_text", side_effect=OSError("note finalization failed")
        ):
            result = import_vault(self.vault, config=self.config)
        self.assertEqual(len(result.failures), 1)
        self.assertTrue(result.failures[0]["serious"])
        self.assertFalse(source.exists())
        self.assertEqual(load_exclusions(self.exclusions_file), {})
        self.assertTrue(list((self.vault / "objects").glob("*/transaction.json")))

    def test_unverifiable_source_does_not_add_exclusion(self) -> None:
        filename = "unverifiable.jpg"
        source = self.add_referenced_file(filename)
        with mock.patch("madobsidian.importer._markdown_plan", side_effect=OSError("planning failed")), mock.patch(
            "madobsidian.importer._source_present", return_value=False
        ):
            result = import_vault(self.vault, config=self.config)
        self.assertEqual(len(result.failures), 1)
        self.assertTrue(result.failures[0]["serious"])
        self.assertTrue(source.exists())
        self.assertFalse(self.exclusions_file.exists())

    def test_failed_exclusions_write_is_reported_after_original_restoration(self) -> None:
        filename = "write-failed.jpg"
        source = self.add_referenced_file(filename)
        with mock.patch("madobsidian.importer.generate_preview", side_effect=OSError("preview failed")), mock.patch(
            "madobsidian.importer.append_exclusion", side_effect=ExclusionsError("disk unavailable")
        ):
            result = import_vault(self.vault, config=self.config)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("madobsidian_exclusions.ini update failed", result.failures[0]["reason"])
        self.assertTrue(source.exists())
        self.assertFalse(self.exclusions_file.exists())

    def test_dry_run_does_not_create_or_modify_exclusions_or_vault(self) -> None:
        filename = "dry-run.jpg"
        source = self.add_referenced_file(filename)
        config_before = self.config_file.read_bytes()
        vault_before = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        result = import_vault(self.vault, dry_run=True, config=self.config)
        vault_after = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(len(result), 1)
        self.assertEqual(vault_before, vault_after)
        self.assertEqual(self.config_file.read_bytes(), config_before)
        self.assertFalse(self.exclusions_file.exists())
        self.assertTrue(source.exists())
        self.assertTrue(list(self.base.glob(".madobsidian-vault-*.lock")))

    def test_ordinary_skips_do_not_create_exclusions(self) -> None:
        hidden = self.vault / "Incoming" / ".hidden.jpg"
        unsupported = self.vault / "Incoming" / "note.txt"
        unreferenced = self.vault / "Incoming" / "unreferenced.jpg"
        hidden.write_bytes(b"hidden")
        unsupported.write_bytes(b"text")
        unreferenced.write_bytes(b"unreferenced")
        (self.vault / "note.md").write_text("No attachment reference", encoding="utf-8")
        result = import_vault(self.vault, config=self.config)
        self.assertEqual(len(result.skipped), 3)
        self.assertEqual(result.ignored, [])
        self.assertFalse(self.exclusions_file.exists())
        self.assertTrue(hidden.exists() and unsupported.exists() and unreferenced.exists())

    def test_unicode_and_punctuation_filename_round_trips_as_json(self) -> None:
        filename = 'Проба "x" =;# [1].jpg'
        append_exclusion(self.exclusions_file, filename)
        self.assertEqual(load_exclusions(self.exclusions_file), {filename_key(filename): filename})
        stored_value = json.dumps(filename, ensure_ascii=False)
        self.assertIn(f'"path": {stored_value}', self.exclusions_file.read_text(encoding="utf-8"))

    def test_malformed_exclusions_fail_after_transaction_recovery_and_before_file_work(self) -> None:
        source = self.add_referenced_file("malformed.jpg")
        self.exclusions_file.write_text("[ignore]\nnot-a-hash = \"malformed.jpg\"\n", encoding="utf-8")
        with mock.patch("madobsidian.importer.generate_preview", side_effect=AssertionError("import attempted")):
            with self.assertRaisesRegex(ImportError, "invalid filename hash key"):
                import_vault(self.vault, config=self.config)
        self.assertTrue(source.exists())

    def test_atomic_updates_preserve_existing_entries_and_remove_temporary_file(self) -> None:
        append_exclusion(self.exclusions_file, "first.jpg")
        append_exclusion(self.exclusions_file, "second.jpg")
        self.assertEqual(set(load_exclusions(self.exclusions_file).values()), {"first.jpg", "second.jpg"})
        self.assertFalse(list(self.exclusions_file.parent.glob(".exclusions-*.tmp")))

    def test_concurrent_exclusion_updates_do_not_lose_entries(self) -> None:
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(target=_append_exclusion_worker, args=(str(self.exclusions_file), name, barrier, results))
            for name in ("one.jpg", "two.jpg")
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual({results.get(timeout=5) for _ in processes}, {("ok", "one.jpg"), ("ok", "two.jpg")})
        self.assertEqual(set(load_exclusions(self.exclusions_file).values()), {"one.jpg", "two.jpg"})
        self.assertTrue(list(self.base.glob("*.exclusions.lock")))

    def test_exclusions_path_is_inside_vault(self) -> None:
        self.assertEqual(exclusion_path(self.vault), self.vault / "madobsidian_exclusions.ini")

    def test_unrelated_content_and_conflicting_edit_survive(self):
        prefix = b"; operator notes\r\n[other]\r\nsetting = keep\r\n"
        self.exclusions_file.write_bytes(prefix)
        append_exclusion(self.exclusions_file, "nested/one.png")
        self.assertTrue(self.exclusions_file.read_bytes().startswith(prefix))
        snapshot = self.exclusions_file.read_bytes()

        def concurrent_update(_fd):
            self.exclusions_file.write_bytes(snapshot + b"; concurrent edit\r\n")

        with mock.patch("madobsidian.exclusions.os.fsync", side_effect=concurrent_update):
            with self.assertRaisesRegex(ExclusionsError, "changed concurrently"):
                append_exclusion(self.exclusions_file, "nested/two.png")
        self.assertEqual(self.exclusions_file.read_bytes(), snapshot + b"; concurrent edit\r\n")


if __name__ == "__main__":
    unittest.main()
