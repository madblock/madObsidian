import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from madobsidian.archive import rebuild_index
from madobsidian.cli import main
from madobsidian.config import ConfigError, DEFAULT_CONFIG, resolve_incoming
from madobsidian.exclusions import append_exclusion, exclusion_path
from madobsidian.filenames import filename_problem, storage_name
from madobsidian.importer import _rename_exclusive, import_vault, recover_transactions


class PhaseTwoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name).resolve() / "vault"
        self.vault.mkdir()

    def test_missing_incoming_and_status(self):
        app = self.vault / ".obsidian" / "app.json"
        app.parent.mkdir()
        for configured in ("Attachments", str(self.vault / "Attachments")):
            app.write_text(json.dumps({"attachmentFolderPath": configured}))
            self.assertEqual(resolve_incoming(self.vault, DEFAULT_CONFIG), self.vault / "Attachments")
        before = list(self.vault.parent.rglob("*"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status", str(self.vault)]), 0)
        self.assertIn("Attachments (missing)", output.getvalue())
        self.assertEqual(list(self.vault.parent.rglob("*")), before)
        for dry_run in (True, False):
            self.assertEqual(import_vault(self.vault, dry_run=dry_run), [])
            self.assertFalse((self.vault / "Attachments").exists())
        logs = (self.vault.parent / "import.log").read_text().splitlines()
        self.assertEqual([json.loads(line)["level"] for line in logs], ["WARNING"] * 2)
        for invalid in ("{", "[]", '{"attachmentFolderPath":null}', '{"attachmentFolderPath":" "}'):
            app.write_text(invalid)
            with self.assertRaises(ConfigError):
                import_vault(self.vault)
        app.write_text("{}")
        self.assertEqual(resolve_incoming(self.vault, DEFAULT_CONFIG), self.vault / "Incoming")

    def test_nested_exclusion_and_active_note_spans(self):
        incoming = self.vault / "Incoming"
        for folder in ("A", "B"):
            (incoming / folder).mkdir(parents=True)
            (incoming / folder / "p.jpg").write_bytes(folder.encode())
        notes = self.vault / "notes"
        notes.mkdir()
        note = notes / "note.md"
        link = "![text](../Incoming/B/p.jpg)"
        wiki = "![[Incoming/B/p.jpg|caption]]"
        inactive = ("---\nexample: " + wiki + "\n---\n<!-- " + link + " -->\n"
                    + chr(96) + wiki + "\n" + link + chr(96) + "\n~~~\n" + wiki + "\n~~~\n"
                    + "    " + wiki + "\n> ~~~\n> " + wiki + "\n> ~~~\n")
        note.write_text(inactive + wiki + "\n" + link)
        append_exclusion(exclusion_path(self.vault), "A/p.jpg")
        batch = import_vault(self.vault)
        self.assertEqual([item["source"] for item in batch.ignored], ["A/p.jpg"])
        row, = batch
        self.assertEqual((self.vault / row["target"]).read_bytes(), b"B")
        self.assertEqual(note.read_text(), inactive + "![["
                         + row["target"] + "|caption]]\n![text](../" + row["target"] + ")")

    def test_portable_component_and_extended_suffix(self):
        for name in ("con.jpg", "aux.txt", "LPT9", "trailing.", "space ", "a:b", "bad?"):
            self.assertTrue(filename_problem(name), name)
        relative = "aux/safe.jpg"
        candidate = storage_name(relative, [relative])
        self.assertRegex(candidate, r"^safe_~[A-Z2-7]{6}\.jpg$")
        extended = storage_name(relative, [relative, candidate.lower()])
        self.assertRegex(extended, r"^safe_~[A-Z2-7]{7}\.jpg$")
        self.assertEqual(extended, storage_name(relative, [candidate.lower(), relative]))
        self.assertTrue(storage_name("aux/test.jpg. ", []).endswith(".jpg"))

    def test_move_refuses_occupied_target(self):
        first, second = self.vault / "source", self.vault / "occupied"
        first.write_bytes(b"source")
        second.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            _rename_exclusive(first, second)
        self.assertEqual(first.read_bytes(), b"source")
        self.assertEqual(second.read_bytes(), b"existing")

    @unittest.skipUnless(os.name == "posix", "requires POSIX filesystem names")
    def test_linux_only_names_move_to_portable_storage(self):
        source = self.vault / "Incoming" / "AUX" / "bad?.jpg. "
        source.parent.mkdir(parents=True)
        source.write_bytes(b"Linux filename")
        note = self.vault / "note.md"
        note.write_text("[image](<Incoming/AUX/bad?.jpg. >)")
        row, = import_vault(self.vault)
        stored = self.vault / row["target"]
        self.assertEqual(stored.read_bytes(), b"Linux filename")
        self.assertIsNone(filename_problem(stored.name))
        self.assertIn(stored.name, note.read_text())
        self.assertFalse(source.exists())

    def test_interrupted_renamed_original_rolls_forward(self):
        incoming = self.vault / "Incoming"
        for folder, leaf in (("A", "leaf.png"), ("B", "LEAF.png")):
            (incoming / folder).mkdir(parents=True)
            (incoming / folder / leaf).write_bytes(folder.encode())
        note = self.vault / "note.md"
        note.write_text("[[Incoming/A/leaf.png]]")
        with mock.patch("madobsidian.importer._atomic_text", side_effect=OSError("interruption")):
            failed = import_vault(self.vault)
        self.assertTrue(failed.failures[0]["serious"])
        recover_transactions(self.vault)
        recover_transactions(self.vault)
        target = self.vault / note.read_text()[2:-2]
        self.assertIn("_~", target.name)
        self.assertEqual(target.read_bytes(), b"A")
        self.assertTrue((incoming / "B" / "LEAF.png").exists())

    def test_incomplete_scan_preserves_index(self):
        objects = self.vault / "objects"
        objects.mkdir()
        (objects / ".syncthing.metadata.json.tmp").write_bytes(b"partial")
        index = self.vault / "index.json"
        index.write_bytes(b"previous index")
        for limit in (0, 100):
            with mock.patch("madobsidian.archive.MAX_OBJECTS", limit):
                self.assertFalse(rebuild_index(self.vault).complete)
            self.assertEqual(index.read_bytes(), b"previous index")
