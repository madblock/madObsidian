"""Terminate a vault worker without running exception handlers or finally blocks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from madobsidian import importer


def worker(vault, operation, point):
    def interrupt(boundary):
        if boundary == point:
            os._exit(83)

    importer._checkpoint = interrupt
    if point.startswith("rollback_copy"):
        def no_links(*args):
            raise OSError("filesystem lacks hard links")
        os.link = no_links
    if operation == "import":
        importer.import_vault(vault)
    elif operation == "index":
        from madobsidian.archive import rebuild_index
        replace = os.replace
        def publish(src, dst):
            interrupt("index_prepared")
            replace(src, dst)
            interrupt("index_replaced")
        os.replace = publish
        rebuild_index(vault)
    else:
        importer.recover_transactions(vault)


class TransactionCrashTests(unittest.TestCase):
    def exercise(self, point, *, published=False, rollback=False, rename=False):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            source = vault / "Incoming" / "original.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"original payload")
            if rename:
                other = source.parent / "other" / "ORIGINAL.png"
                other.parent.mkdir()
                other.write_bytes(b"other media")
            notes = [vault / "first.md", vault / "second.md"]
            link = "Incoming/original.png" if rename else "original.png"
            for note in notes:
                note.write_text(f"[[{link}]]", encoding="utf-8")

            def run(operation, boundary="", expected=0):
                result = subprocess.run([sys.executable, __file__, str(vault), operation, boundary],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)

            if rollback:
                run("import", "journal_moved", 83)
            run("recover" if rollback else "import", point, 83)
            originals = [p for p in vault.rglob("*.png") if p.name != "ORIGINAL.png"]
            self.assertTrue(originals)
            if point == "rollback_copy_opened":
                self.assertEqual(source.read_bytes(), b"")
                stage, = vault.glob("objects/.incomplete-*")
                journal = stage / "transaction.json"
                before = journal.read_bytes()
                for _ in range(2):
                    run("recover", expected=1)
                    self.assertEqual(journal.read_bytes(), before)
                    self.assertEqual((stage / "shared" / source.name).read_bytes(), b"original payload")
                    self.assertFalse((vault / "madobsidian_exclusions.ini").exists())
                    self.assertTrue(all(note.read_text() == f"[[{link}]]" for note in notes))
                return
            self.assertTrue(all(p.read_bytes() == b"original payload" for p in originals))
            journals = list(vault.rglob("transaction.json"))
            if point in {"source_verified", "stage_created", "layout_created", "journal_temp_moving",
                         "rollback_journal_removed", "journal_removed"}:
                self.assertFalse(journals)
            else:
                self.assertEqual(len(journals), 1)
                self.assertEqual(json.loads(journals[0].read_bytes())["source"], "Incoming/original.png")
            run("recover")
            self.assertFalse(list(vault.rglob("transaction.json")))
            self.assertFalse(list(vault.rglob(".incomplete-*")))
            original, = (p for p in vault.rglob("*.png") if p.name != "ORIGINAL.png")
            self.assertEqual(original.read_bytes(), b"original payload")
            self.assertEqual(original == source, not published)
            target = original.relative_to(vault).as_posix() if published else link
            if rename and published:
                self.assertIn("_~", original.name)
            for note in notes:
                self.assertEqual(note.read_text(encoding="utf-8"), f"[[{target}]]")

            def snapshot():
                return {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*") if p.is_file()}

            recovered = snapshot()
            run("recover")
            self.assertEqual(snapshot(), recovered)

    def test_import_boundaries(self):
        for point in ("source_verified", "stage_created", "layout_created", "journal_temp_moving",
                      "journal_moving", "source_moved", "journal_temp_moved", "journal_moved",
                      "preview_finished", "metadata_written", "journal_temp_prepared", "journal_prepared"):
            with self.subTest(point=point):
                self.exercise(point)
        for point in ("object_published", "journal_temp_published", "journal_published", "note_temp_written", "note_updated",
                      "journal_temp_completed",
                      "journal_completed", "journal_removed"):
            with self.subTest(point=point):
                self.exercise(point, published=True)

    def test_rollback_boundaries(self):
        for point in ("journal_temp_rolling_back", "journal_rolling_back", "source_restored",
                      "staged_original_removed", "journal_temp_rolled_back", "journal_rolled_back",
                      "rollback_copy_opened", "rollback_copy_written", "rollback_exclusion_written",
                      "stage_payload_removed", "rollback_journal_removed"):
            with self.subTest(point=point):
                self.exercise(point, rollback=True)

    def test_portable_name_transaction(self):
        for point, published in (("source_moved", False), ("object_published", True), ("note_updated", True)):
            with self.subTest(point=point):
                self.exercise(point, rename=True, published=published)

    def test_disposable_index_publication(self):
        for point in ("index_prepared", "index_replaced"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                vault = Path(directory) / "vault"
                (vault / "Incoming").mkdir(parents=True)
                (vault / "Incoming" / "p.png").write_bytes(b"index fixture")
                note = vault / "p.md"
                note.write_text("[[p.png]]")
                row, = importer.import_vault(vault)
                index = vault / "index.json"
                index.write_bytes(b"old index")
                args = [sys.executable, __file__, str(vault)]
                self.assertEqual(subprocess.run(args + ["index", point], timeout=30).returncode, 83)
                self.assertEqual(index.read_bytes() == b"old index", point == "index_prepared")
                for _ in range(2):
                    subprocess.run(args + ["recover", ""], check=True, timeout=30)
                    subprocess.run(args + ["index", ""], check=True, timeout=30)
                    self.assertEqual(json.loads(index.read_bytes())["objects"][0]["path"], row["target"])
                    self.assertEqual((vault / row["target"]).read_bytes(), b"index fixture")
                    self.assertEqual(note.read_text(), f"[[{row['target']}]]")
                    self.assertFalse(list(vault.rglob("transaction.json")))


if __name__ == "__main__":
    worker(*sys.argv[1:])
