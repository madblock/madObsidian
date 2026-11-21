"""Archive verification and recovery tests using disposable temporary archives."""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from PIL import Image

from madobsidian.archive import rebuild_index, scan_archive
from madobsidian.cli import main


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.objects = self.archive / "objects"
        self.objects.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_object(self, content: bytes = b"archive bytes", **changes) -> tuple[Path, dict]:
        identifier = str(uuid.uuid4())
        directory = self.objects / identifier
        directory.mkdir()
        original = directory / "original.multi.part.bin"
        original.write_bytes(content)
        metadata = {
            "id": identifier,
            "original_filename": original.name,
            "extension": ".bin",
            "mime_type": "application/octet-stream",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata.update(changes)
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return directory, metadata

    def test_valid_archive_verifies(self) -> None:
        directory, _ = self.add_object()
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 1)
        self.assertEqual(report.objects[0].relative_path, f"objects/{directory.name}/original.multi.part.bin")
        self.assertEqual(report.problems, ())

    def test_cli_verify_is_read_only(self) -> None:
        self.add_object()
        index = self.archive / "index.json"
        index.write_text('{"keep": true}\n', encoding="utf-8")
        before = {str(p.relative_to(self.archive)): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(["verify", str(self.archive)])
        after = {str(p.relative_to(self.archive)): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()}
        self.assertEqual(result, 0)
        self.assertIn("Verified 1 object(s)", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(before, after)

    def test_cli_verify_returns_nonzero_and_reports_problem(self) -> None:
        directory, _ = self.add_object()
        (directory / "metadata.json").write_text("{bad", encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["verify", str(self.archive)])
        self.assertEqual(result, 1)
        self.assertIn(directory.name, stderr.getvalue())

    def test_index_creation_and_deleted_index_rebuild_are_identical(self) -> None:
        self.add_object(b"first")
        self.add_object(b"second")
        report = rebuild_index(self.archive)
        index = self.archive / "index.json"
        first_bytes = index.read_bytes()
        first_data = json.loads(first_bytes)
        self.assertEqual(first_data["version"], 1)
        self.assertEqual(len(first_data["objects"]), 2)
        self.assertTrue(all(set(row) == {"metadata", "path"} for row in first_data["objects"]))
        self.assertEqual(len(report.objects), 2)
        index.unlink()
        rebuild_index(self.archive)
        self.assertEqual(index.read_bytes(), first_bytes)

    def test_invalid_json_and_missing_required_field_are_reported(self) -> None:
        first, _ = self.add_object()
        second, metadata = self.add_object()
        (first / "metadata.json").write_text("{not json", encoding="utf-8")
        del metadata["mime_type"]
        (second / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 0)
        self.assertEqual(len(report.problems), 2)
        self.assertTrue(any("cannot read metadata" in problem.reason for problem in report.problems))
        self.assertTrue(any("mime_type" in problem.reason for problem in report.problems))

    def test_invalid_uuid_and_directory_metadata_id_mismatch(self) -> None:
        wrong_id_dir, _ = self.add_object(id="not-a-uuid")
        mismatch_dir, _ = self.add_object(id=str(uuid.uuid4()))
        invalid_dir = self.objects / "not-a-uuid"
        invalid_dir.mkdir()
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 0)
        self.assertEqual(len(report.problems), 2)
        self.assertTrue(any("not a valid UUID" in p.reason for p in report.problems))
        self.assertTrue(any("does not match" in p.reason for p in report.problems))
        self.assertTrue(wrong_id_dir.exists())
        self.assertTrue(mismatch_dir.exists())

    def test_invalid_size_hash_timestamp_and_field_types(self) -> None:
        cases = [
            ("size_bytes", -1, "nonnegative"),
            ("size_bytes", True, "integer"),
            ("sha256", "a" * 63, "64 hexadecimal"),
            ("imported_at", "yesterday", "ISO 8601"),
            ("imported_at", "2024-01-01T00:00:00", "UTC"),
            ("extension", 4, "must be a string"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory(dir=self.root) as temp:
                    archive = Path(temp) / "archive"
                    (archive / "objects").mkdir(parents=True)
                    identifier = str(uuid.uuid4())
                    directory = archive / "objects" / identifier
                    directory.mkdir()
                    content = b"archive bytes"
                    (directory / "original.multi.part.bin").write_bytes(content)
                    metadata = {
                        "id": identifier,
                        "original_filename": "original.multi.part.bin",
                        "extension": ".bin",
                        "mime_type": "application/octet-stream",
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "imported_at": datetime.now(timezone.utc).isoformat(),
                    }
                    metadata[field] = value
                    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                    report = scan_archive(archive)
                    self.assertEqual(len(report.objects), 0)
                    self.assertIn(expected, report.problems[0].reason)

    def test_canonical_timestamp_is_accepted_and_alternatives_rejected(self) -> None:
        accepted = "2024-01-02T03:04:05.123456+00:00"
        directory, _ = self.add_object(imported_at=accepted)
        self.assertEqual(scan_archive(self.archive).problems, ())
        for timestamp in (
            "2024-01-02T03:04:05.123456+01:00",
            "2024-01-02T03:04:05.123456Z",
            "2024-01-02 03:04:05.123456+00:00",
        ):
            with self.subTest(timestamp=timestamp):
                data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
                data["imported_at"] = timestamp
                (directory / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
                report = scan_archive(self.archive)
                self.assertTrue(any("canonical UTC" in p.reason for p in report.problems))

    def test_extension_and_mime_type_consistency(self) -> None:
        for changes, expected in (
            ({"extension": ".jpg"}, "does not match original_filename"),
            ({"mime_type": ""}, "non-empty string"),
            ({"mime_type": "   "}, "non-empty string"),
        ):
            with self.subTest(changes=changes):
                archive = self.root / f"case-{len(list(self.root.iterdir()))}" / "archive"
                objects = archive / "objects"
                objects.mkdir(parents=True)
                identifier = str(uuid.uuid4())
                directory = objects / identifier
                directory.mkdir()
                content = b"content"
                (directory / "file.bin").write_bytes(content)
                metadata = {
                    "id": identifier,
                    "original_filename": "file.bin",
                    "extension": ".bin",
                    "mime_type": "application/octet-stream",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "imported_at": "2024-01-02T03:04:05+00:00",
                    **changes,
                }
                (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                report = scan_archive(archive)
                self.assertEqual(len(report.objects), 0)
                self.assertIn(expected, report.problems[0].reason)

    def test_case_insensitive_reserved_metadata_filename_is_rejected(self) -> None:
        directory, _ = self.add_object(original_filename="METADATA.JSON")
        report = scan_archive(self.archive)
        self.assertTrue(any(p.object_id == directory.name and "reserved metadata.json" in p.reason
                            for p in report.problems))

    def test_noncanonical_uuid_directory_is_reported(self) -> None:
        identifier = str(uuid.uuid4())
        directory = self.objects / identifier.upper()
        directory.mkdir()
        content = b"content"
        (directory / "file.bin").write_bytes(content)
        metadata = {
            "id": identifier,
            "original_filename": "file.bin",
            "extension": ".bin",
            "mime_type": "application/octet-stream",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "imported_at": "2024-01-02T03:04:05+00:00",
        }
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        report = scan_archive(self.archive)
        self.assertTrue(any(p.object_id == directory.name and "canonical lowercase UUID" in p.reason
                            for p in report.problems))

    def test_uuid_shaped_noncanonical_directory_is_reported(self) -> None:
        identifier = str(uuid.uuid4())
        directory = self.objects / ("{" + identifier + "}")
        directory.mkdir()
        report = scan_archive(self.archive)
        self.assertTrue(any(p.object_id == directory.name and "canonical lowercase UUID" in p.reason
                            for p in report.problems))

    def test_uuid_named_object_directory_symlink_is_not_followed(self) -> None:
        outside = self.root / "outside-object"
        outside.mkdir()
        (outside / "marker").write_text("outside", encoding="utf-8")
        link = self.objects / str(uuid.uuid4())
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Cannot create object-directory symlink in this environment: {error}")
        report = scan_archive(self.archive)
        self.assertFalse(report.objects)
        self.assertTrue(any(p.object_id == link.name and "symbolic link" in p.reason for p in report.problems))
        self.assertEqual((outside / "marker").read_text(encoding="utf-8"), "outside")

    def test_objects_directory_symlink_is_not_followed(self) -> None:
        self.objects.rmdir()
        outside = self.root / "outside-objects"
        outside.mkdir()
        (outside / "marker").write_text("outside", encoding="utf-8")
        try:
            os.symlink(outside, self.objects, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Cannot create objects-directory symlink in this environment: {error}")
        report = scan_archive(self.archive)
        self.assertFalse(report.objects)
        self.assertTrue(any(p.object_id == "objects" and "symbolic link" in p.reason for p in report.problems))
        self.assertEqual((outside / "marker").read_text(encoding="utf-8"), "outside")

    def test_path_traversal_and_separator_filenames_are_rejected(self) -> None:
        for filename in ("../outside.txt", "a/child.txt", r"a\child.txt", ".", "..", "C:outside.txt"):
            directory, _ = self.add_object(original_filename=filename)
            report = scan_archive(self.archive)
            self.assertTrue(any(p.object_id == directory.name and "filename" in p.reason for p in report.problems))

    def test_filename_with_embedded_double_dots_is_accepted(self) -> None:
        directory, _ = self.add_object(original_filename="safe..name.txt")
        (directory / "original.multi.part.bin").rename(directory / "safe..name.txt")
        metadata_path = directory / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["extension"] = ".txt"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 1)
        self.assertEqual(report.problems, ())

    def test_missing_original_modified_original_and_size_mismatch(self) -> None:
        missing, _ = self.add_object()
        (missing / "original.multi.part.bin").unlink()
        modified, _ = self.add_object(b"initial")
        (modified / "original.multi.part.bin").write_bytes(b"changed")
        size_mismatch, metadata = self.add_object(b"size")
        metadata["size_bytes"] += 1
        (size_mismatch / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        report = scan_archive(self.archive)
        reasons = {problem.object_id: problem.reason for problem in report.problems}
        self.assertIn("missing", reasons[missing.name])
        self.assertIn("SHA-256", reasons[modified.name])
        self.assertIn("size", reasons[size_mismatch.name])

    def test_original_file_symlink_is_reported_without_following(self) -> None:
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        directory, metadata = self.add_object(b"inside")
        original = directory / metadata["original_filename"]
        original.unlink()
        try:
            os.symlink(outside, original)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Could not create symbolic link on this Windows/filesystem setup: {error}")
        report = scan_archive(self.archive)
        self.assertTrue(any(p.object_id == directory.name and "symbolic link" in p.reason for p in report.problems))
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_metadata_symlink_is_reported_without_following(self) -> None:
        directory, _ = self.add_object()
        external_metadata = self.root / "external.json"
        external_metadata.write_text("{}", encoding="utf-8")
        metadata_path = directory / "metadata.json"
        metadata_path.unlink()
        try:
            os.symlink(external_metadata, metadata_path)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Could not create symbolic link on this Windows/filesystem setup: {error}")
        report = scan_archive(self.archive)
        self.assertTrue(any(p.object_id == directory.name and "metadata.json is a symbolic link" in p.reason
                            for p in report.problems))
        self.assertEqual(external_metadata.read_text(encoding="utf-8"), "{}")

    def test_incomplete_directory_is_reported_and_other_objects_scanned(self) -> None:
        self.add_object()
        (self.objects / ".incomplete-abandoned").mkdir()
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 1)
        self.assertTrue(any("abandoned importer temporary" in p.reason for p in report.problems))

    def test_valid_objects_reported_even_when_another_is_invalid(self) -> None:
        good, _ = self.add_object()
        bad, _ = self.add_object()
        (bad / "metadata.json").write_text("{}", encoding="utf-8")
        report = scan_archive(self.archive)
        self.assertEqual([obj.metadata["id"] for obj in report.objects], [good.name])
        self.assertEqual(len(report.problems), 1)

    def test_invalid_object_does_not_replace_existing_index(self) -> None:
        directory, _ = self.add_object()
        index = self.archive / "index.json"
        index.write_bytes(b"previous-index\n")
        (directory / "metadata.json").write_text("not json", encoding="utf-8")
        report = rebuild_index(self.archive)
        self.assertTrue(report.problems)
        self.assertEqual(index.read_bytes(), b"previous-index\n")

    def test_missing_configured_objects_directory_does_not_replace_index(self) -> None:
        self.add_object()
        index = self.archive / "index.json"
        index.write_bytes(b"previous-index\n")
        report = rebuild_index(self.archive, objects_directory="objects-typo")
        self.assertTrue(any("configured objects directory does not exist" in p.reason for p in report.problems))
        self.assertEqual(index.read_bytes(), b"previous-index\n")

    def test_corrupt_registered_image_is_rejected_as_eligible_image(self) -> None:
        identifier = str(uuid.uuid4())
        directory = self.objects / identifier
        (directory / "originals").mkdir(parents=True)
        original = directory / "originals" / "corrupt.jpg"
        original.write_bytes(b"not a JPEG" + b"\0" * (256_000 - len(b"not a JPEG")))
        content = original.read_bytes()
        metadata = {
            "id": identifier, "original_filename": original.name, "original_path": "originals/corrupt.jpg",
            "extension": ".jpg", "mime_type": "image/jpeg", "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        report = scan_archive(self.archive)
        self.assertTrue(any("eligible image original cannot be decoded" in p.reason for p in report.problems))

    def test_index_replacement_failure_preserves_previous_index_and_cleans_temp(self) -> None:
        self.add_object()
        index = self.archive / "index.json"
        index.write_bytes(b"previous-index\n")
        with mock.patch("madobsidian.archive.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                rebuild_index(self.archive)
        self.assertEqual(index.read_bytes(), b"previous-index\n")
        self.assertFalse(any(p.name.startswith(".index-incomplete-") for p in self.archive.iterdir()))

    def test_unrelated_root_files_are_ignored(self) -> None:
        self.add_object()
        (self.archive / "notes.txt").write_text("ignored", encoding="utf-8")
        (self.archive / "odd-directory").mkdir()
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 1)
        self.assertEqual(report.problems, ())

    def test_new_large_image_requires_a_decodable_preview_and_blocks_index(self) -> None:
        identifier = str(uuid.uuid4())
        directory = self.objects / identifier
        (directory / "originals").mkdir(parents=True)
        original = directory / "originals" / "large.png"
        Image.new("RGB", (64, 64), (10, 20, 30)).save(original, format="PNG")
        with original.open("ab") as stream:
            stream.write(b"\0" * (256_000 - original.stat().st_size))
        content = original.read_bytes()
        metadata = {
            "id": identifier, "original_filename": original.name, "original_path": "originals/large.png",
            "extension": ".png", "mime_type": "image/png", "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (directory / "previews").mkdir()
        Image.new("RGB", (32, 32), (30, 20, 10)).save(directory / "previews" / "preview.webp", format="WEBP")
        self.assertEqual(scan_archive(self.archive).problems, ())
        (directory / "previews" / "preview.webp").unlink()
        index = self.archive / "index.json"
        index.write_bytes(b"keep previous index")
        report = rebuild_index(self.archive)
        self.assertTrue(any("expected preview is missing" in p.reason for p in report.problems))
        self.assertEqual(index.read_bytes(), b"keep previous index")
        (directory / "previews" / "preview.webp").write_bytes(b"not webp")
        self.assertTrue(any("cannot be decoded" in p.reason for p in scan_archive(self.archive).problems))

    def test_legacy_large_object_without_preview_remains_verifiable(self) -> None:
        content = b"legacy bytes" + b"\0" * (256_000 - len(b"legacy bytes"))
        directory, _ = self.add_object(content)
        report = scan_archive(self.archive)
        self.assertEqual(len(report.objects), 1)
        self.assertEqual(report.problems, ())

    def test_dangling_transaction_journal_symlink_is_reported(self) -> None:
        directory, _ = self.add_object()
        journal = directory / "transaction.json"
        try:
            os.symlink(self.root / "does-not-exist.json", journal)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Cannot create dangling symlink in this environment: {error}")
        report = scan_archive(self.archive)
        self.assertFalse(report.objects)
        self.assertTrue(any("transaction.json is a symbolic link" in p.reason for p in report.problems))


if __name__ == "__main__":
    unittest.main()
