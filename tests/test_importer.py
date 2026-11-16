"""Tests for local archive imports using disposable temporary files."""

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from madobsidian.importer import ImportError, import_path
from madobsidian.cli import main


class ImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assert_valid_object(self, source: Path, result) -> dict:
        object_dir = self.archive / "objects" / result.metadata.id
        copied = object_dir / source.name
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        data = json.loads((object_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(data["id"], result.metadata.id)
        self.assertEqual(data["original_filename"], source.name)
        self.assertEqual(data["extension"], source.suffix)
        self.assertEqual(data["size_bytes"], source.stat().st_size)
        self.assertEqual(data["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(data["imported_at"].endswith("+00:00"))
        self.assertTrue(data["mime_type"])
        return data

    def test_import_file_writes_content_and_metadata(self) -> None:
        source = self.root / "photo.test.jpg"
        source.write_bytes(b"disposable image bytes")
        results = import_path(source, self.archive)
        self.assertEqual(len(results), 1)
        data = self._assert_valid_object(source, results[0])
        self.assertEqual(data["mime_type"], "image/jpeg")

    def test_import_directory_recursively_in_deterministic_order(self) -> None:
        source_dir = self.root / "input"
        (source_dir / "nested").mkdir(parents=True)
        first = source_dir / "z.txt"
        second = source_dir / "nested" / "a.unknownext"
        first.write_text("z", encoding="utf-8")
        second.write_text("a", encoding="utf-8")
        results = import_path(source_dir, self.archive)
        self.assertEqual([r.metadata.original_filename for r in results], ["a.unknownext", "z.txt"])
        self.assertEqual(results[0].metadata.mime_type, "application/octet-stream")
        self._assert_valid_object(second, results[0])
        self._assert_valid_object(first, results[1])

    def test_empty_unicode_filename_and_no_extension(self) -> None:
        source = self.root / "файл"
        source.write_bytes(b"")
        result = import_path(source, self.archive)[0]
        self.assertEqual(result.metadata.size_bytes, 0)
        self.assertEqual(result.metadata.extension, "")
        self._assert_valid_object(source, result)

    def test_repeated_import_creates_distinct_objects(self) -> None:
        source = self.root / "same.bin"
        source.write_bytes(b"same")
        first, second = import_path(source, self.archive)[0], import_path(source, self.archive)[0]
        self.assertNotEqual(first.metadata.id, second.metadata.id)
        self.assertTrue(first.object_path.exists())
        self.assertTrue(second.object_path.exists())

    def test_uuid_collision_does_not_overwrite_existing_object(self) -> None:
        source = self.root / "item.txt"
        source.write_text("new", encoding="utf-8")
        collision = "00000000-0000-4000-8000-000000000001"
        objects = self.archive / "objects"
        existing = objects / collision
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        # uuid objects stringify through their canonical representation.
        with mock.patch("madobsidian.importer.uuid.uuid4", side_effect=[
            uuid.UUID(collision),
            uuid.UUID("00000000-0000-4000-8000-000000000002"),
        ]):
            result = import_path(source, self.archive)[0]
        self.assertEqual(result.metadata.id, "00000000-0000-4000-8000-000000000002")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_copy_failure_leaves_no_completed_or_incomplete_object(self) -> None:
        source = self.root / "item.txt"
        source.write_text("content", encoding="utf-8")
        with mock.patch("madobsidian.importer.shutil.copyfile", side_effect=OSError("copy interrupted")):
            with self.assertRaises(OSError):
                import_path(source, self.archive)
        objects = self.archive / "objects"
        self.assertEqual(list(objects.iterdir()), [])

    def test_verification_failure_leaves_no_object(self) -> None:
        source = self.root / "item.txt"
        source.write_text("content", encoding="utf-8")
        with mock.patch("madobsidian.importer._sha256", side_effect=["source-hash", "wrong-hash"]):
            with self.assertRaises(ImportError):
                import_path(source, self.archive)
        self.assertEqual(list((self.archive / "objects").iterdir()), [])

    def test_missing_source_is_rejected(self) -> None:
        with self.assertRaises(ImportError):
            import_path(self.root / "missing", self.archive)

    def test_reserved_metadata_filename_is_rejected_safely(self) -> None:
        source = self.root / "metadata.json"
        source.write_text("source", encoding="utf-8")
        with self.assertRaises(ImportError):
            import_path(source, self.archive)
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertFalse((self.archive / "objects").exists())

    def test_cli_end_to_end_batch_preserves_every_file_byte_for_byte(self) -> None:
        source_dir = self.root / "batch"
        (source_dir / "nested" / "deeper").mkdir(parents=True)
        files = {
            source_dir / "ordinary.txt": b"ordinary contents\x00\xff",
            source_dir / "nested" / "empty.bin": b"",
            source_dir / "nested" / "report.final.v2.csv": b"a,b\n1,2\n",
            source_dir / "nested" / "deeper" / "café-猫.dat": b"unicode name",
            source_dir / "nested" / "deeper" / "two.parts.name.tar": b"multi dot",
        }
        for path, content in files.items():
            path.write_bytes(content)
        output = __import__("io").StringIO()
        with mock.patch("sys.stdout", output):
            result = main(["import", str(source_dir), str(self.archive)])
        self.assertEqual(result, 0)
        self.assertIn("Imported 5 file(s).", output.getvalue())
        object_dirs = list((self.archive / "objects").iterdir())
        self.assertEqual(len(object_dirs), len(files))
        actual_names = set()
        for object_dir in object_dirs:
            data = json.loads((object_dir / "metadata.json").read_text(encoding="utf-8"))
            name = data["original_filename"]
            original = object_dir / name
            source = next(path for path in files if path.name == name)
            actual_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            self.assertEqual(actual_hash, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(actual_hash, data["sha256"])
            self.assertEqual(original.read_bytes(), source.read_bytes())
            actual_names.add(name)
        self.assertEqual(actual_names, {path.name for path in files})

    def test_file_symlink_outside_source_is_not_followed(self) -> None:
        source_dir = self.root / "input"
        source_dir.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("must not be imported", encoding="utf-8")
        try:
            os.symlink(outside, source_dir / "linked.txt")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Could not create file symbolic link on this Windows/filesystem setup: {error}")
        import_path(source_dir, self.archive)
        self.assertFalse((self.archive / "objects").exists())

    def test_individual_file_symlink_is_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_bytes(b"must remain untouched")
        link = self.root / "linked.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Could not create file symbolic link on this Windows/filesystem setup: {error}")
        with self.assertRaisesRegex(ImportError, "Symbolic links cannot be imported"):
            import_path(link, self.archive)
        self.assertEqual(outside.read_bytes(), b"must remain untouched")
        self.assertFalse((self.archive / "objects").exists())

    def test_directory_symlink_outside_source_is_not_followed(self) -> None:
        source_dir = self.root / "input"
        source_dir.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("must not be imported", encoding="utf-8")
        try:
            os.symlink(outside, source_dir / "linked-dir", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Could not create directory symbolic link on this Windows/filesystem setup: {error}")
        import_path(source_dir, self.archive)
        self.assertFalse((self.archive / "objects").exists())

    def test_inaccessible_file_permission_error_leaves_no_completed_object(self) -> None:
        source = self.root / "denied.txt"
        source.write_text("disposable", encoding="utf-8")
        original_open = Path.open

        def denied_open(path, *args, **kwargs):
            if path == source:
                raise PermissionError("simulated access denied")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", denied_open):
            with self.assertRaises(PermissionError):
                import_path(source, self.archive)
        self.assertFalse((self.archive / "objects").exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "disposable")

    def test_directory_traversal_error_is_reported_by_cli(self) -> None:
        import io
        source_dir = self.root / "input"
        source_dir.mkdir()
        stderr = io.StringIO()
        with mock.patch.object(Path, "rglob", side_effect=PermissionError("simulated traversal denial")):
            with mock.patch("sys.stderr", stderr):
                result = main(["import", str(source_dir), str(self.archive)])
        self.assertEqual(result, 1)
        self.assertIn("simulated traversal denial", stderr.getvalue())
        self.assertFalse((self.archive / "objects").exists())

    def test_partial_batch_failure_preserves_prior_object_and_omits_failed_object(self) -> None:
        import shutil
        source_dir = self.root / "input"
        source_dir.mkdir()
        completed = source_dir / "a-good.txt"
        failed = source_dir / "b-fails.txt"
        completed.write_bytes(b"completed")
        failed.write_bytes(b"will fail")
        original_copy = shutil.copyfile

        def fail_second(source, destination, *args, **kwargs):
            if Path(source) == failed:
                raise OSError("simulated second-file copy failure")
            return original_copy(source, destination, *args, **kwargs)

        with mock.patch("madobsidian.importer.shutil.copyfile", side_effect=fail_second):
            with self.assertRaises(OSError):
                import_path(source_dir, self.archive)
        object_dirs = list((self.archive / "objects").iterdir())
        completed_dirs = [p for p in object_dirs if not p.name.startswith(".")]
        self.assertEqual(len(completed_dirs), 1)
        copied = completed_dirs[0] / completed.name
        self.assertEqual(copied.read_bytes(), completed.read_bytes())
        self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(),
                         hashlib.sha256(completed.read_bytes()).hexdigest())
        self.assertFalse((completed_dirs[0] / failed.name).exists())
        self.assertFalse(any(p.name.startswith(".incomplete-") for p in object_dirs))
        self.assertEqual(failed.read_bytes(), b"will fail")


if __name__ == "__main__":
    unittest.main()
