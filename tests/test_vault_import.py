"""Synthetic vault integration tests."""

import contextlib
import io
import json
import hashlib
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from pathlib import Path

from madobsidian.archive import rebuild_index, scan_archive
from madobsidian.cli import main as cli_main
from madobsidian import importer
from madobsidian.config import load_config
from madobsidian.importer import import_vault


class VaultImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        (self.vault / "Incoming").mkdir(parents=True)
        (self.vault / "Folder").mkdir()
        self.local_state = Path(self.temp.name) / "local-state"
        self.state_environment = mock.patch.dict(
            os.environ,
            {"APPDATA": str(self.local_state), "XDG_CONFIG_HOME": str(self.local_state)},
        )
        self.state_environment.start()
        self.addCleanup(self.state_environment.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_small_file_layout_and_markdown_rewrite(self):
        source = self.vault / "Incoming" / "café [x].jpg"
        source.write_bytes(b"small")
        note = self.vault / "Folder" / "note.md"
        note.write_text("![[Incoming/café [x].jpg|caption]]\n[[Incoming/café [x].jpg]]", encoding="utf-8")
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer.generate_video_preview", return_value=None
        ):
            result = import_vault(self.vault)[0]
        original = self.vault / result["target"]
        self.assertEqual(original.read_bytes(), b"small")
        data = json.loads((original.parent.parent / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(data["original_path"], "shared/café [x].jpg")
        self.assertEqual(data["sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
        rewritten = note.read_text(encoding="utf-8")
        self.assertEqual(rewritten.count("objects/" + str(result["id"]) + "/shared/"), 2)
        self.assertFalse(source.exists())
        self.assertEqual(len(scan_archive(self.vault).objects), 1)
        rebuild_index(self.vault)
        self.assertFalse((self.vault.parent / "import.log").exists())

    def test_large_file_rewrites_embed_and_adds_stable_uri(self):
        source = self.vault / "Incoming" / "video.mp4"
        source.write_bytes(b"x" * 256_000)
        note = self.vault / "note.md"
        note.write_text("![[Incoming/video.mp4|clip]]", encoding="utf-8")
        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer.generate_video_preview", return_value=Path("preview.mp4")
        ):
            result = import_vault(self.vault)[0]
        value = note.read_text(encoding="utf-8")
        self.assertIn(f"objects/{result['id']}/previews/preview.mp4|clip", value)
        self.assertIn(f"madobsidian://{result['id']}", value)
        self.assertEqual((self.vault / result["target"]).stat().st_size, 256_000)

    def test_dry_run_is_read_only_and_reports_note(self):
        source = self.vault / "Incoming" / "a.jpg"
        source.write_text("bytes", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("![[Incoming/a.jpg]]", encoding="utf-8")
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        rows = import_vault(self.vault, dry_run=True)
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertIn("note.md", rows[0]["changes"])
        self.assertFalse((self.vault / "objects").exists())

    def test_referenced_supported_image_and_video_are_imported(self):
        image = self.vault / "Incoming" / "picture.JPG"
        video = self.vault / "Incoming" / "clip.MP4"
        image.write_bytes(b"synthetic image bytes")
        video.write_bytes(b"synthetic video bytes")
        note = self.vault / "note.md"
        note.write_text("![[Incoming/picture.JPG]]\n![[Incoming/clip.MP4]]", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1]:
            rows = import_vault(self.vault)
        self.assertEqual(len(rows), 2)
        self.assertFalse(image.exists())
        self.assertFalse(video.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("[0-9a-f]*"))), 2)

    def test_unreferenced_supported_media_is_skipped_and_left_untouched(self):
        source = self.vault / "Incoming" / "unreferenced.jpg"
        content = b"do not import"
        source.write_bytes(content)
        (self.vault / "note.md").write_text("No attachment here.", encoding="utf-8")
        rows = import_vault(self.vault)
        self.assertEqual(rows, [])
        self.assertEqual(source.read_bytes(), content)
        self.assertFalse((self.vault / "objects").exists())

    def test_hidden_referenced_and_unreferenced_files_are_skipped(self):
        referenced = self.vault / "Incoming" / ".hidden.jpg"
        unreferenced = self.vault / "Incoming" / ".syncthing.0. Introduction.mp4.tmp"
        referenced.write_bytes(b"hidden media")
        unreferenced.write_bytes(b"temporary data")
        (self.vault / "note.md").write_text("![[Incoming/.hidden.jpg]]", encoding="utf-8")
        self.assertEqual(import_vault(self.vault), [])
        self.assertEqual(referenced.read_bytes(), b"hidden media")
        self.assertEqual(unreferenced.read_bytes(), b"temporary data")
        self.assertFalse((self.vault / "objects").exists())

    def test_temporary_and_unsupported_extensions_are_skipped_even_when_referenced(self):
        temporary = self.vault / "Incoming" / "video.mp4.tmp"
        unsupported = self.vault / "Incoming" / "document.txt"
        custom = self.vault / "Incoming" / "clip.custom"
        temporary.write_bytes(b"temporary")
        unsupported.write_bytes(b"text")
        custom.write_bytes(b"custom video mime")
        (self.vault / "note.md").write_text(
            "![[Incoming/video.mp4.tmp]]\n![[Incoming/document.txt]]\n![[Incoming/clip.custom]]",
            encoding="utf-8",
        )
        with mock.patch("madobsidian.video_previews.mimetypes.guess_type", return_value=("video/x-custom", None)):
            self.assertEqual(import_vault(self.vault), [])
        self.assertTrue(temporary.is_file())
        self.assertTrue(unsupported.is_file())
        self.assertTrue(custom.is_file())
        self.assertFalse((self.vault / "objects").exists())

    def test_ambiguity_is_preserved_for_referenced_media_candidates(self):
        composed_name = "café.jpg"
        decomposed_name = "cafe\u0301.jpg"
        first = self.vault / "Incoming" / composed_name
        second = self.vault / "Incoming" / decomposed_name
        first.write_bytes(b"first")
        if second.exists():
            self.skipTest("filesystem normalizes the two Unicode filename forms")
        second.write_bytes(b"second")
        note = self.vault / "note.md"
        note.write_text(
            f"![[Incoming/{composed_name}]]\n![[Incoming/{decomposed_name}]]\n[[{composed_name}]]",
            encoding="utf-8",
        )
        with self._without_previews()[0], self._without_previews()[1]:
            rows = import_vault(self.vault)
        self.assertEqual(len(rows), 2)
        self.assertIn("[[café.jpg]]", note.read_text(encoding="utf-8"))
        self.assertEqual(sum(len(row["ambiguities"]) for row in rows), 1)

    def test_basename_only_unicode_ambiguity_keeps_both_candidates(self):
        composed_name = "caf\u00e9.jpg"
        decomposed_name = "cafe\u0301.jpg"
        first = self.vault / "Incoming" / composed_name
        second = self.vault / "Incoming" / decomposed_name
        first.write_bytes(b"first original")
        if second.exists():
            self.skipTest("filesystem normalizes the two Unicode filename forms")
        second.write_bytes(b"second original")
        note = self.vault / "note.md"
        original_reference = f"![[{composed_name}]]"
        note.write_text(original_reference, encoding="utf-8")

        with self._without_previews()[0], self._without_previews()[1]:
            rows = import_vault(self.vault)

        self.assertEqual(len(rows), 2)
        self.assertEqual(note.read_text(encoding="utf-8"), original_reference)
        ambiguities = [ambiguity for row in rows for ambiguity in row["ambiguities"]]
        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(set(ambiguities[0]["candidates"]), {composed_name, decomposed_name})
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("[0-9a-f]*/shared/*.jpg"))), 2)

    def test_note_failure_keeps_source_and_rerun_recovers_without_duplicate(self):
        source = self.vault / "Incoming" / "recover.jpg"
        source.write_text("recover me", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("![[Incoming/recover.jpg]]", encoding="utf-8")
        with mock.patch("madobsidian.importer._atomic_text", side_effect=OSError("simulated note failure")):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertFalse(source.exists())
        self.assertEqual((self.vault / "objects" / list((self.vault / "objects").glob("[0-9a-f]*"))[0].name / "shared" / "recover.jpg").read_text(encoding="utf-8"), "recover me")
        self.assertEqual(len(list((self.vault / "objects").iterdir())), 1)
        import_vault(self.vault)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").iterdir())), 1)
        self.assertFalse(list((self.vault / "objects").glob("*/transaction.json")))

    def _without_previews(self):
        return (
            mock.patch("madobsidian.importer.generate_preview", return_value=None),
            mock.patch("madobsidian.importer.generate_video_preview", return_value=None),
        )

    def _assert_one_file_error_log_record(self, source):
        log_path = self.vault.parent / "import.log"
        records = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(records), 1)
        record = json.loads(records[0])
        self.assertEqual(record["level"], "ERROR")
        self.assertEqual(Path(record["source"]).resolve(), source.resolve())
        return record

    def test_source_inspection_failure_is_logged_once(self):
        source = self.vault / "Incoming" / "hash-error.jpg"
        source.write_bytes(b"source")
        (self.vault / "note.md").write_text("![[Incoming/hash-error.jpg]]", encoding="utf-8")
        with mock.patch("madobsidian.importer._sha256", side_effect=OSError("source hash failed")):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        record = self._assert_one_file_error_log_record(source)
        self.assertIn("source hash failed", record["description"])
        self.assertEqual(source.read_bytes(), b"source")

    def test_markdown_planning_failure_is_logged_once(self):
        source = self.vault / "Incoming" / "markdown-error.jpg"
        source.write_bytes(b"source")
        with mock.patch("madobsidian.importer._markdown_plan", side_effect=OSError("markdown read failed")):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        record = self._assert_one_file_error_log_record(source)
        self.assertIn("markdown read failed", record["description"])
        self.assertEqual(source.read_bytes(), b"source")

    def test_objects_directory_setup_failure_is_logged_once(self):
        source = self.vault / "Incoming" / "objects-error.jpg"
        source.write_bytes(b"source")
        (self.vault / "note.md").write_text("![[Incoming/objects-error.jpg]]", encoding="utf-8")
        path_type = type(source)
        real_mkdir = path_type.mkdir

        def fail_objects_setup(path, *args, **kwargs):
            if path.name == "objects" and path.parent.name == "vault":
                raise OSError("objects directory unavailable")
            return real_mkdir(path, *args, **kwargs)

        with mock.patch.object(path_type, "mkdir", autospec=True, side_effect=fail_objects_setup):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        record = self._assert_one_file_error_log_record(source)
        self.assertIn("objects directory unavailable", record["description"])
        self.assertEqual(source.read_bytes(), b"source")

    def test_partial_note_updates_recover_and_preserve_unrelated_edits(self):
        source = self.vault / "Incoming" / "multi.jpg"
        source.write_bytes(b"data")
        first, second = self.vault / "a.md", self.vault / "b.md"
        first.write_text("![[Incoming/multi.jpg]]", encoding="utf-8")
        second.write_text("[[Incoming/multi.jpg]]", encoding="utf-8")
        real_write = importer._atomic_text
        calls = 0

        def write_then_crash(path, value):
            nonlocal calls
            real_write(path, value)
            calls += 1
            if calls == 1:
                raise OSError("simulated crash after first note")

        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._atomic_text", side_effect=write_then_crash
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/shared/multi.jpg"))), 1)
        self.assertIn("objects/", first.read_text(encoding="utf-8"))
        self.assertIn("Incoming/multi.jpg", second.read_text(encoding="utf-8"))
        second.write_text(second.read_text(encoding="utf-8") + "\nUser note retained.\n", encoding="utf-8")
        import_vault(self.vault)
        self.assertFalse(source.exists())
        self.assertIn("User note retained.", second.read_text(encoding="utf-8"))
        self.assertEqual(len(list((self.vault / "objects").glob("[0-9a-f]*"))), 1)

    def test_changed_reference_conflicts_without_overwriting_user_edit(self):
        source = self.vault / "Incoming" / "conflict.jpg"
        source.write_text("data", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("[[Incoming/conflict.jpg]]", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._atomic_text", side_effect=OSError("stop before edit")
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        note.write_text("[[Incoming/renamed-by-user.jpg]]", encoding="utf-8")
        with self.assertRaisesRegex(importer.ImportError, "recovery conflict"):
            import_vault(self.vault)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/shared/conflict.jpg"))), 1)
        self.assertEqual(note.read_text(encoding="utf-8"), "[[Incoming/renamed-by-user.jpg]]")
        self.assertTrue(list((self.vault / "objects").glob("*/transaction.json")))

    def test_recovery_after_note_edits_before_journal_cleanup_is_idempotent(self):
        source = self.vault / "Incoming" / "after-notes.jpg"
        source.write_bytes(b"x" * 256_000)
        note = self.vault / "note.md"
        note.write_text("![[Incoming/after-notes.jpg]]", encoding="utf-8")
        real_unlink = Path.unlink

        def fail_journal_unlink(path, *args, **kwargs):
            if path.name == "transaction.json":
                raise OSError("simulated interruption before journal cleanup")
            return real_unlink(path, *args, **kwargs)

        with self._without_previews()[0], self._without_previews()[1], mock.patch.object(
            Path, "unlink", fail_journal_unlink
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        first_text = note.read_text(encoding="utf-8")
        self.assertEqual(first_text.count("madobsidian://"), 1)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/originals/after-notes.jpg"))), 1)
        import_vault(self.vault)
        self.assertFalse(source.exists())
        final_text = note.read_text(encoding="utf-8")
        self.assertEqual(final_text.count("objects/"), 1)
        self.assertEqual(final_text.count("madobsidian://"), 1)

    def _leave_pending_import(self, filename="pending.jpg"):
        source = self.vault / "Incoming" / filename
        source.write_text("pending bytes", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text(f"![[Incoming/{filename}]]", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._atomic_text", side_effect=OSError("simulated interruption")
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertFalse(source.exists())
        return source

    def test_changed_objects_directory_blocks_duplicate_import(self):
        source = self._leave_pending_import()
        config_path = self.vault / "alternate.ini"
        config_path.write_text("[general]\nobjects_directory = archive-v2\n", encoding="utf-8")
        config = load_config(config_path)
        with self.assertRaisesRegex(importer.ImportError, "outside the configured objects directory"):
            import_vault(self.vault, config=config)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/shared/pending.jpg"))), 1)
        self.assertFalse((self.vault / "archive-v2").exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/transaction.json"))), 1)

    def test_changed_threshold_refuses_unsafe_pending_recovery(self):
        source = self._leave_pending_import()
        config_path = self.vault / "alternate.ini"
        config_path.write_text("[general]\nsmall_file_threshold_bytes = 1\n", encoding="utf-8")
        config = load_config(config_path)
        with self.assertRaisesRegex(importer.ImportError, "size threshold"):
            import_vault(self.vault, config=config)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/transaction.json"))), 1)

    def test_changed_incoming_directory_refuses_unsafe_pending_recovery(self):
        source = self._leave_pending_import()
        (self.vault / "NewIncoming").mkdir()
        config_path = self.vault / "alternate.ini"
        config_path.write_text("[general]\nincoming_directory = NewIncoming\n", encoding="utf-8")
        config = load_config(config_path)
        with self.assertRaisesRegex(importer.ImportError, "different incoming_directory"):
            import_vault(self.vault, config=config)
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("*/transaction.json"))), 1)

    def test_dry_run_reports_pending_recovery_without_mutating(self):
        source = self.vault / "Incoming" / "pending.jpg"
        source.write_text("data", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("[[Incoming/pending.jpg]]", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._atomic_text", side_effect=OSError("leave a pending transaction")
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        rows = import_vault(self.vault, dry_run=True)
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(rows.recoveries), 1)
        self.assertIn("pending", rows.recoveries[0]["action"])

    def test_dry_run_with_no_pending_recoveries_counts_only_planned_imports(self):
        source = self.vault / "Incoming" / "clean.jpg"
        source.write_bytes(b"planned bytes")
        (self.vault / "note.md").write_text("![[Incoming/clean.jpg]]", encoding="utf-8")

        rows = import_vault(self.vault, dry_run=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.recoveries, [])
        self.assertEqual(rows[0]["source"], "Incoming/clean.jpg")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = cli_main(["import", str(self.vault), "--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("Would import 1 file(s); pending recoveries 0;", stdout.getvalue())
        self.assertNotIn("Pending recovery:", stdout.getvalue())

    def test_dry_run_separates_pending_recoveries_from_planned_imports_and_cli_reports_both(self):
        self._leave_pending_import("pending.jpg")
        fresh = self.vault / "Incoming" / "fresh.jpg"
        fresh.write_bytes(b"fresh bytes")
        (self.vault / "fresh.md").write_text("![[Incoming/fresh.jpg]]", encoding="utf-8")

        before = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        rows = import_vault(self.vault, dry_run=True)
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "Incoming/fresh.jpg")
        self.assertEqual(len(rows.recoveries), 1)
        self.assertEqual(rows.recoveries[0]["source"], "Incoming/pending.jpg")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = cli_main(["import", str(self.vault), "--dry-run"])
        self.assertEqual(status, 0)
        output = stdout.getvalue()
        self.assertIn("Would import 1 file(s); pending recoveries 1; skipped 0; ignored 0; failed 0.", output)
        self.assertIn("Pending recovery:", output)
        self.assertIn("Incoming/pending.jpg", output)
        self.assertIn("Incoming/fresh.jpg", output)

    def test_real_import_with_pending_recovery_retains_existing_result_semantics(self):
        self._leave_pending_import("pending.jpg")
        fresh = self.vault / "Incoming" / "fresh.jpg"
        fresh.write_bytes(b"fresh bytes")
        (self.vault / "fresh.md").write_text("![[Incoming/fresh.jpg]]", encoding="utf-8")

        stdout = io.StringIO()
        with self._without_previews()[0], self._without_previews()[1], contextlib.redirect_stdout(stdout):
            status = cli_main(["import", str(self.vault)])
        self.assertEqual(status, 0)
        output = stdout.getvalue()
        self.assertIn("Imported 1 file(s); skipped 0; ignored 0; failed 0.", output)
        self.assertNotIn("pending recoveries", output)
        self.assertFalse(fresh.exists())
        self.assertEqual(len(list((self.vault / "objects").glob("[0-9a-f]*"))), 2)
        self.assertFalse(list((self.vault / "objects").glob("*/transaction.json")))

    def test_configured_incoming_directory_is_used_for_path_matching(self):
        media = self.vault / "Media"
        media.mkdir()
        source = media / "clip.jpg"
        source.write_text("media", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("[[Media/clip.jpg]]\n[[Incoming/clip.jpg]]", encoding="utf-8")
        config_path = self.vault / "settings.ini"
        config_path.write_text("[general]\nincoming_directory = Media\n", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1]:
            result = import_vault(self.vault, config=load_config(config_path))[0]
        content = note.read_text(encoding="utf-8")
        self.assertIn(f"objects/{result['id']}/shared/clip.jpg", content)
        self.assertIn("[[Incoming/clip.jpg]]", content)
        self.assertFalse(source.exists())

    def test_post_move_hash_mismatch_preserves_original_in_staging(self):
        source = self.vault / "Incoming" / "mismatch.jpg"
        source.write_bytes(b"preserve after mismatch")
        (self.vault / "note.md").write_text("![[Incoming/mismatch.jpg]]", encoding="utf-8")
        real_hash = importer._sha256

        def mismatch_staged(path):
            if path.name == source.name and any(part.startswith(".incomplete-") for part in path.parts):
                return "0" * 64
            return real_hash(path)

        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._sha256", side_effect=mismatch_staged
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertTrue(failed.failures[0]["serious"])
        self.assertFalse(source.exists())
        staged = list((self.vault / "objects").glob(".incomplete-*/shared/mismatch.jpg"))
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].read_bytes(), b"preserve after mismatch")

    def test_interruption_immediately_after_move_is_recoverable_and_repeatable(self):
        source = self.vault / "Incoming" / "immediate.jpg"
        source.write_bytes(b"moved before verification")
        (self.vault / "note.md").write_text("![[Incoming/immediate.jpg]]", encoding="utf-8")
        real_hash = importer._sha256

        def interrupt_hash(path):
            if path.name == source.name and any(part.startswith(".incomplete-") for part in path.parts):
                raise OSError("simulated interruption immediately after rename")
            return real_hash(path)

        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._sha256", side_effect=interrupt_hash
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertTrue(failed.failures[0]["serious"])
        self.assertFalse(source.exists())
        recovered, _ = importer.recover_transactions(self.vault)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(source.read_bytes(), b"moved before verification")
        repeated, _ = importer.recover_transactions(self.vault)
        self.assertEqual(repeated, [])
        self.assertFalse(list((self.vault / "objects").glob(".incomplete-*")))

    def test_exception_after_move_preserves_stage_when_expected_target_disappears(self):
        source = self.vault / "Incoming" / "displaced.jpg"
        source.write_bytes(b"original moved to an unexpected staging path")
        (self.vault / "note.md").write_text("![[Incoming/displaced.jpg]]", encoding="utf-8")
        path_type = type(source)
        real_rename = path_type.rename

        def move_then_displace(path, target):
            if Path(path).name == source.name and Path(path).parent.name == "Incoming":
                real_rename(path, target)
                unexpected = target.parent / "displaced-original.bin"
                real_rename(target, unexpected)
                raise OSError("simulated interruption after move and target displacement")
            return real_rename(path, target)

        with self._without_previews()[0], self._without_previews()[1], mock.patch.object(
            path_type, "rename", autospec=True, side_effect=move_then_displace
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertTrue(failed.failures[0]["serious"])

        stages = list((self.vault / "objects").glob(".incomplete-*"))
        self.assertEqual(len(stages), 1)
        self.assertFalse(source.exists())
        self.assertFalse((stages[0] / "shared" / source.name).exists())
        self.assertEqual(
            (stages[0] / "shared" / "displaced-original.bin").read_bytes(),
            b"original moved to an unexpected staging path",
        )
        self.assertTrue((stages[0] / "transaction.json").is_file())

    def test_failed_rename_preserves_source_notes_and_logs_once_in_vault_parent(self):
        source = self.vault / "Incoming" / "rename-error.jpg"
        source.write_bytes(b"original remains")
        note = self.vault / "note.md"
        note.write_text("![[Incoming/rename-error.jpg]]\n", encoding="utf-8")
        original_note = note.read_bytes()
        path_type = type(source)
        real_rename = path_type.rename

        def fail_source_rename(path, target):
            if Path(path).name == source.name and Path(path).parent.name == "Incoming":
                raise OSError("simulated rename denied")
            return real_rename(path, target)

        with mock.patch("madobsidian.importer.generate_preview", return_value=None), mock.patch(
            "madobsidian.importer.generate_video_preview", return_value=None
        ), mock.patch.object(path_type, "rename", autospec=True, side_effect=fail_source_rename), mock.patch(
            "madobsidian.importer.shutil.copyfile", side_effect=AssertionError("copy fallback must not run")
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)

        self.assertEqual(source.read_bytes(), b"original remains")
        self.assertEqual(note.read_bytes(), original_note)
        self.assertEqual(list((self.vault / "objects").glob("[0-9a-f]*")), [])
        self.assertEqual(list((self.vault / "objects").glob(".incomplete-*")), [])
        log_path = self.vault.parent / "import.log"
        self.assertTrue(log_path.is_file())
        records = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(records), 1)
        record = json.loads(records[0])
        self.assertEqual(record["level"], "ERROR")
        self.assertEqual(Path(record["source"]).resolve(), source.resolve())
        self.assertIn("simulated rename denied", record["description"])
        self.assertIsNotNone(datetime.fromisoformat(record["timestamp"]).utcoffset())

    def test_log_failure_does_not_mask_failed_rename(self):
        source = self.vault / "Incoming" / "log-error.jpg"
        source.write_bytes(b"keep me")
        (self.vault / "note.md").write_text("![[Incoming/log-error.jpg]]", encoding="utf-8")
        path_type = type(source)
        real_rename = path_type.rename
        real_open = path_type.open

        def fail_source_rename(path, target):
            if Path(path).name == source.name and Path(path).parent.name == "Incoming":
                raise OSError("original rename failure")
            return real_rename(path, target)

        def fail_log_open(path, *args, **kwargs):
            if Path(path).name == "import.log":
                raise PermissionError("log unavailable")
            return real_open(path, *args, **kwargs)

        with mock.patch.object(path_type, "rename", autospec=True, side_effect=fail_source_rename), mock.patch.object(
            path_type, "open", autospec=True, side_effect=fail_log_open
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertIn("original rename failure", failed.failures[0]["reason"])
        self.assertEqual(source.read_bytes(), b"keep me")

    def test_recovery_after_verified_move_before_preview_processing(self):
        source = self.vault / "Incoming" / "verified-move.jpg"
        source.write_bytes(b"verified then interrupted")
        (self.vault / "note.md").write_text("![[Incoming/verified-move.jpg]]", encoding="utf-8")
        real_write_journal = importer._write_journal

        def interrupt_after_move(path, data):
            if data.get("phase") == "moved":
                raise OSError("simulated interruption after verified move")
            return real_write_journal(path, data)

        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._write_journal", side_effect=interrupt_after_move
        ):
            failed = import_vault(self.vault)
        self.assertEqual(len(failed.failures), 1)
        self.assertFalse(failed.failures[0]["serious"])
        self.assertTrue(source.exists())
        self.assertFalse(list((self.vault / "objects").glob(".incomplete-*")))
        recovered, _ = importer.recover_transactions(self.vault)
        self.assertEqual(recovered, [])
        self.assertEqual(source.read_bytes(), b"verified then interrupted")
        repeated, _ = importer.recover_transactions(self.vault)
        self.assertEqual(repeated, [])

    def test_abandoned_staging_is_removed_only_with_verified_source(self):
        source = self.vault / "Incoming" / "staged.jpg"
        source.write_bytes(b"verified")
        (self.vault / "note.md").write_text("![[Incoming/staged.jpg]]", encoding="utf-8")
        staging = self.vault / "objects" / ".incomplete-crash"
        (staging / "shared").mkdir(parents=True)
        (staging / "shared" / source.name).write_bytes(source.read_bytes())
        with self._without_previews()[0], self._without_previews()[1]:
            result = import_vault(self.vault)[0]
        self.assertFalse(staging.exists())
        self.assertFalse(source.exists())
        self.assertTrue((self.vault / result["target"]).is_file())

    def test_legacy_incomplete_root_copy_requires_matching_source(self):
        source = self.vault / "Incoming" / "old-layout.jpg"
        source.write_bytes(b"old bytes")
        (self.vault / "note.md").write_text("![[Incoming/old-layout.jpg]]", encoding="utf-8")
        staging = self.vault / "objects" / ".incomplete-legacy"
        staging.mkdir(parents=True)
        (staging / source.name).write_bytes(b"old bytes")
        with self._without_previews()[0], self._without_previews()[1]:
            import_vault(self.vault)
        self.assertFalse(staging.exists())
        self.assertFalse(source.exists())

    def test_unverified_staging_is_preserved_when_source_changed(self):
        source = self.vault / "Incoming" / "stale.jpg"
        source.write_bytes(b"new source")
        staging = self.vault / "objects" / ".incomplete-unverified"
        (staging / "shared").mkdir(parents=True)
        (staging / "shared" / source.name).write_bytes(b"old archived source")
        old = b"old archived source"
        journal = {"id": "00000000-0000-4000-8000-000000000001", "source": "Incoming/stale.jpg",
                   "original_path": "shared/stale.jpg", "sha256": hashlib.sha256(old).hexdigest(),
                   "size_bytes": len(old), "markdown": {}, "phase": "moving", "source_mode": "move"}
        (staging / "transaction.json").write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaises(importer.ImportError):
            import_vault(self.vault)
        self.assertEqual(source.read_bytes(), b"new source")
        self.assertEqual((staging / "shared" / source.name).read_bytes(), old)

    def _leave_immediate_move_pending(self, filename="target.jpg"):
        source = self.vault / "Incoming" / filename
        original = b"pending bytes"
        source.write_bytes(original)
        (self.vault / "note.md").write_text(
            f"![[Incoming/{filename}]]", encoding="utf-8"
        )
        real_hash = importer._sha256

        def interrupt_hash(path):
            if path.name == source.name and any(
                part.startswith(".incomplete-") for part in path.parts
            ):
                raise OSError("simulated interruption immediately after rename")
            return real_hash(path)

        with self._without_previews()[0], self._without_previews()[1], mock.patch(
            "madobsidian.importer._sha256", side_effect=interrupt_hash
        ):
            failed = import_vault(self.vault)

        self.assertEqual(len(failed.failures), 1)
        self.assertFalse(source.exists())
        self.assertEqual(
            len(list((self.vault / "objects").glob(".incomplete-*/transaction.json"))),
            1,
        )
        return source, original

    def test_targeted_recovery_skips_unrelated_malformed_journal(self):
        source, original = self._leave_immediate_move_pending("target.jpg")

        unrelated = self.vault / "objects" / ".incomplete-unrelated"
        unrelated.mkdir()
        (unrelated / "transaction.json").write_text(
            json.dumps({
                "id": "not-a-uuid",
                "source": "Incoming/unrelated.jpg",
            }),
            encoding="utf-8",
        )

        recovered, _ = importer.recover_transactions(
            self.vault, only_source="Incoming/target.jpg"
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue((unrelated / "transaction.json").exists())

    def test_targeted_recovery_rejects_malformed_matching_journal(self):
        source, _ = self._leave_immediate_move_pending("target.jpg")

        journals = list(
            (self.vault / "objects").glob(".incomplete-*/transaction.json")
        )
        self.assertEqual(len(journals), 1)

        journal_path = journals[0]
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["id"] = "not-a-uuid"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")

        with self.assertRaisesRegex(importer.ImportError, "invalid UUID"):
            importer.recover_transactions(
                self.vault, only_source="Incoming/target.jpg"
            )

        self.assertFalse(source.exists())
        self.assertTrue(journal_path.exists())

    def test_unfiltered_recovery_still_rejects_unrelated_malformed_journal(self):
        source, original = self._leave_immediate_move_pending("target.jpg")

        unrelated = self.vault / "objects" / ".incomplete-unrelated"
        unrelated.mkdir()
        (unrelated / "transaction.json").write_text(
            json.dumps({
                "id": "not-a-uuid",
                "source": "Incoming/unrelated.jpg",
            }),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            importer.ImportError, "Malformed transaction journal"
        ):
            importer.recover_transactions(self.vault)

        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue((unrelated / "transaction.json").exists())

    def test_legacy_copy_mode_journal_is_rejected_without_recovery_actions(self):
        source = self.vault / "Incoming" / "old-journal.jpg"
        source.write_bytes(b"incoming original")
        staging = self.vault / "objects" / ".incomplete-old-journal"
        (staging / "shared").mkdir(parents=True)
        (staging / "shared" / source.name).write_bytes(b"staged copy")
        journal = {
            "id": "00000000-0000-4000-8000-000000000001",
            "source": "Incoming/old-journal.jpg",
            "original_path": "shared/old-journal.jpg",
            "sha256": hashlib.sha256(b"staged copy").hexdigest(),
            "size_bytes": len(b"staged copy"),
            "markdown": {},
            "phase": "staging",
        }
        (staging / "transaction.json").write_text(json.dumps(journal), encoding="utf-8")

        with self.assertRaisesRegex(importer.ImportError, "Malformed transaction journal"):
            importer.recover_transactions(self.vault)

        self.assertEqual(source.read_bytes(), b"incoming original")
        self.assertEqual((staging / "shared" / source.name).read_bytes(), b"staged copy")

    def test_malformed_journal_paths_cannot_touch_external_files(self):
        source = self.vault / "Incoming" / "safe.jpg"
        source.write_bytes(b"safe")
        external = self.vault.parent / "outside.jpg"
        external.write_bytes(b"must survive")
        obj = self.vault / "objects" / "00000000-0000-4000-8000-000000000001"
        (obj / "shared").mkdir(parents=True)
        original = obj / "shared" / "safe.jpg"
        original.write_bytes(b"safe")
        record = {"id": obj.name, "source": "../outside.jpg", "original_path": "shared/safe.jpg",
                  "sha256": hashlib.sha256(b"safe").hexdigest(), "size_bytes": 4,
                  "markdown": {}, "phase": "published"}
        (obj / "transaction.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(importer.ImportError):
            import_vault(self.vault)
        self.assertEqual(external.read_bytes(), b"must survive")
        self.assertEqual(source.read_bytes(), b"safe")

    def test_fenced_inline_code_duplicate_paths_and_unicode_normalization(self):
        source_name = "caf\u00e9 [photo].jpg"
        source = self.vault / "Incoming" / source_name
        source.write_bytes(b"small")
        note = self.vault / "note.md"
        note.write_text(
            "`![[Incoming/café [photo].jpg]]`\n"
            "```markdown\n![[Incoming/café [photo].jpg]]\n```\n"
            "[[Other/café [photo].jpg]]\n"
            "[[Else/Incoming/café [photo].jpg]]\n"
            "![[Incoming/cafe\u0301 [photo].jpg|alias]]\n",
            encoding="utf-8",
        )
        with self._without_previews()[0], self._without_previews()[1]:
            result = import_vault(self.vault)[0]
        updated = note.read_text(encoding="utf-8")
        self.assertIn("`![[Incoming/café [photo].jpg]]`", updated)
        self.assertIn("```markdown\n![[Incoming/café [photo].jpg]]\n```", updated)
        self.assertIn("[[Other/café [photo].jpg]]", updated)
        self.assertIn("[[Else/Incoming/café [photo].jpg]]", updated)
        self.assertIn(f"objects/{result['id']}/shared/café [photo].jpg|alias", updated)
        self.assertEqual(updated.count("objects/"), 1)

    def test_crlf_newline_style_is_preserved(self):
        source = self.vault / "Incoming" / "line.jpg"
        source.write_text("x", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_bytes(b"first\r\n![[Incoming/line.jpg]]\r\nlast\r\n")
        with self._without_previews()[0], self._without_previews()[1]:
            import_vault(self.vault)
        raw = note.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_supported_video_extensions_choose_mp4_preview(self):
        for name in ("clip.3gp", "clip.flv"):
            incoming = self.vault / "Incoming" / name
            incoming.write_text("x", encoding="utf-8")
            (self.vault / "note.md").write_text(f"![[Incoming/{name}]]", encoding="utf-8")
            plan = importer._markdown_plan(self.vault, name, "00000000-0000-4000-8000-000000000001", True)
            self.assertIn("preview.mp4", json.dumps(plan))
            incoming.unlink()

    def test_unique_basename_reference_rewrites_with_alias_and_heading(self):
        source = self.vault / "Incoming" / "unique.jpg"
        source.write_bytes(b"image placeholder")
        note = self.vault / "note.md"
        note.write_text("![[unique.jpg#Section|caption]]", encoding="utf-8")
        with self._without_previews()[0], self._without_previews()[1]:
            result = import_vault(self.vault)[0]
        updated = note.read_text(encoding="utf-8")
        self.assertIn(f"![[objects/{result['id']}/shared/unique.jpg#Section|caption]]", updated)
        self.assertEqual(result["ambiguities"], [])

    def test_ambiguous_basename_is_reported_and_left_unchanged(self):
        note = self.vault / "note.md"
        original = "![[same.jpg|caption]]"
        note.write_text(original, encoding="utf-8")
        ambiguous = []
        edits = importer._markdown_plan(
            self.vault, "same.jpg", "00000000-0000-4000-8000-000000000001", False,
            eligible_names=["same.jpg", "same.jpg"], ambiguities=ambiguous,
        )
        self.assertEqual(edits, {})
        self.assertEqual(note.read_text(encoding="utf-8"), original)
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0]["candidates"], ["same.jpg", "same.jpg"])

    def test_unmatched_basename_is_left_unchanged(self):
        note = self.vault / "note.md"
        original = "[[not-imported.png]]"
        note.write_text(original, encoding="utf-8")
        ambiguous = []
        edits = importer._markdown_plan(
            self.vault, "other.png", "00000000-0000-4000-8000-000000000001", False,
            eligible_names=["other.png"], ambiguities=ambiguous,
        )
        self.assertEqual(edits, {})
        self.assertEqual(ambiguous, [])
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_dry_run_and_actual_import_share_basename_matching(self):
        source = self.vault / "Incoming" / "same-match.jpg"
        source.write_text("data", encoding="utf-8")
        note = self.vault / "note.md"
        note.write_text("[[same-match.jpg]]", encoding="utf-8")
        dry_rows = import_vault(self.vault, dry_run=True)
        self.assertIn("note.md", dry_rows[0]["changes"])
        with self._without_previews()[0], self._without_previews()[1]:
            actual = import_vault(self.vault)[0]
        self.assertIn(f"objects/{actual['id']}/shared/same-match.jpg", note.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
