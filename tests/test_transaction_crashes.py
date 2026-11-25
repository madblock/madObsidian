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
    if operation == "import":
        importer.import_vault(vault)
    else:
        importer.recover_transactions(vault)


class TransactionCrashTests(unittest.TestCase):
    def exercise(self, point, *, published=False, rollback=False):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            source = vault / "Incoming" / "original.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"original payload")
            notes = [vault / "first.md", vault / "second.md"]
            for note in notes:
                note.write_text("[[original.png]]", encoding="utf-8")

            def run(operation, boundary="", expected=0):
                result = subprocess.run([sys.executable, __file__, str(vault), operation, boundary],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)

            if rollback:
                run("import", "journal_moved", 83)
            run("recover" if rollback else "import", point, 83)
            originals = list(vault.rglob("original.png"))
            self.assertTrue(originals)
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
            original, = vault.rglob("original.png")
            self.assertEqual(original.read_bytes(), b"original payload")
            self.assertEqual(original == source, not published)
            target = original.relative_to(vault).as_posix() if published else "original.png"
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
        for point in ("object_published", "journal_temp_published", "journal_published", "note_updated",
                      "journal_temp_completed",
                      "journal_completed", "journal_removed"):
            with self.subTest(point=point):
                self.exercise(point, published=True)

    def test_rollback_boundaries(self):
        for point in ("journal_temp_rolling_back", "journal_rolling_back", "source_restored",
                      "staged_original_removed", "journal_temp_rolled_back", "journal_rolled_back",
                      "stage_payload_removed", "rollback_journal_removed"):
            with self.subTest(point=point):
                self.exercise(point, rollback=True)


if __name__ == "__main__":
    worker(*sys.argv[1:])
