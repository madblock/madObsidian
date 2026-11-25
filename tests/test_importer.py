"""Move-only import fixtures and metadata checks."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest import mock

from madobsidian.importer import import_vault


def import_fixture(source, vault):
    """Place a disposable copy in Incoming and import through the vault entry point."""
    incoming = vault / "Incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, incoming / source.name)
    (vault / "test-note.md").write_text(f"[[{source.name}]]", encoding="utf-8")
    return import_vault(vault)


class ImporterTests(unittest.TestCase):
    def test_external_file_and_directory_links_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "vault" / "Incoming"
            incoming.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            media = outside / "photo.jpg"
            media.write_bytes(b"preserve")
            try:
                (incoming / "file.jpg").symlink_to(media)
                (incoming / "folder").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(str(error))
            (incoming.parent / "note.md").write_text("[[file.jpg]] [[Incoming/folder/photo.jpg]]")
            self.assertEqual(import_vault(incoming.parent), [])
            self.assertEqual(media.read_bytes(), b"preserve")
            self.assertFalse((incoming.parent / "objects").exists())

    def test_occupied_planned_uuid_preserves_both_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            source = root / "photo.jpg"
            source.write_bytes(b"first")
            first, = import_fixture(source, vault)
            source.write_bytes(b"second")
            with mock.patch("madobsidian.importer.uuid.uuid4", return_value=uuid.UUID(first["id"])):
                failed = import_fixture(source, vault)
            self.assertEqual(len(failed.failures), 1)
            self.assertEqual((vault / first["target"]).read_bytes(), b"first")
            self.assertEqual((vault / "Incoming" / "photo.jpg").read_bytes(), b"second")

    def test_imported_metadata_matches_preserved_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "safe..name.png"
            source.write_bytes(b"archive me")
            vault = Path(directory) / "vault"
            row, = import_fixture(source, vault)
            original = vault / row["target"]
            metadata = json.loads((original.parent.parent / "metadata.json").read_bytes())
            self.assertEqual(original.read_bytes(), source.read_bytes())
            self.assertEqual(metadata["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(metadata["original_filename"], source.name)
            self.assertEqual(metadata["id"], row["id"])
            self.assertFalse((vault / "Incoming" / source.name).exists())
