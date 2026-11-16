"""Validation, verification, and index recovery for local archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from madobsidian.filenames import filename_problem


@dataclass(frozen=True)
class ArchiveObject:
    metadata: dict[str, Any]
    relative_path: str


@dataclass(frozen=True)
class ArchiveProblem:
    object_id: str
    reason: str

    def __str__(self) -> str:
        return f"{self.object_id}: {self.reason}"


@dataclass(frozen=True)
class ScanReport:
    objects: tuple[ArchiveObject, ...]
    problems: tuple[ArchiveProblem, ...]


class ArchiveError(Exception):
    """Raised when an archive cannot be safely scanned or indexed."""


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUIRED = {
    "id": str,
    "original_filename": str,
    "extension": str,
    "mime_type": str,
    "sha256": str,
    "imported_at": str,
}


def _hash_file(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("original file is a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _validate_metadata(data: Any, directory_id: str) -> str | None:
    if not isinstance(data, dict):
        return "metadata must be a JSON object"
    for field, expected_type in _REQUIRED.items():
        if field not in data:
            return f"metadata is missing required field '{field}'"
        if not isinstance(data[field], expected_type):
            return f"metadata field '{field}' must be a string"
    if not data["mime_type"].strip():
        return "metadata field 'mime_type' must be a non-empty string"
    if "size_bytes" not in data:
        return "metadata is missing required field 'size_bytes'"
    if isinstance(data["size_bytes"], bool) or not isinstance(data["size_bytes"], int):
        return "metadata field 'size_bytes' must be an integer"
    if data["size_bytes"] < 0:
        return "metadata field 'size_bytes' must be nonnegative"
    try:
        parsed_id = uuid.UUID(data["id"])
    except (ValueError, AttributeError):
        return "metadata field 'id' is not a valid UUID"
    if str(parsed_id) != data["id"].lower():
        return "metadata field 'id' must use canonical UUID syntax"
    if directory_id != directory_id.lower() or str(uuid.UUID(directory_id)) != directory_id:
        return "object directory name must use canonical lowercase UUID syntax"
    if data["id"] != directory_id:
        return "metadata ID does not match the object directory name"
    if not _SHA256.fullmatch(data["sha256"]):
        return "metadata field 'sha256' must be exactly 64 hexadecimal characters"
    invalid_filename = filename_problem(data["original_filename"])
    if invalid_filename:
        return invalid_filename
    try:
        timestamp = datetime.fromisoformat(data["imported_at"])
    except ValueError:
        return "metadata field 'imported_at' is not a valid ISO 8601 timestamp"
    if (
        timestamp.tzinfo is None
        or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp)
        or timestamp.isoformat() != data["imported_at"]
        or not data["imported_at"].endswith("+00:00")
    ):
        return "metadata field 'imported_at' must use the canonical UTC ISO 8601 format"
    if data["extension"] != Path(data["original_filename"]).suffix:
        return "metadata field 'extension' does not match original_filename"
    return None


def _scan_object(directory: Path) -> ArchiveObject | ArchiveProblem:
    object_id = directory.name
    if directory.is_symlink():
        return ArchiveProblem(object_id, "object directory is a symbolic link")
    if not directory.is_dir():
        return ArchiveProblem(object_id, "object entry is not a directory")
    metadata_path = directory / "metadata.json"
    try:
        if metadata_path.is_symlink():
            return ArchiveProblem(object_id, "metadata.json is a symbolic link")
        if not metadata_path.is_file():
            return ArchiveProblem(object_id, "metadata.json is missing or is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(metadata_path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return ArchiveProblem(object_id, "metadata.json is not a regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                data = json.load(stream)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return ArchiveProblem(object_id, f"cannot read metadata.json: {error}")

    validation_error = _validate_metadata(data, object_id)
    if validation_error:
        return ArchiveProblem(object_id, validation_error)
    original = directory / data["original_filename"]
    try:
        if original.is_symlink():
            return ArchiveProblem(object_id, "original file is a symbolic link")
        info = original.lstat()
        if not stat.S_ISREG(info.st_mode):
            return ArchiveProblem(object_id, "original file is missing or is not a regular file")
        if info.st_size != data["size_bytes"]:
            return ArchiveProblem(object_id, "original file size does not match metadata")
        actual_hash = _hash_file(original)
    except FileNotFoundError:
        return ArchiveProblem(object_id, "original file is missing")
    except OSError as error:
        return ArchiveProblem(object_id, f"cannot inspect original file: {error}")
    except ValueError as error:
        return ArchiveProblem(object_id, str(error))
    if actual_hash != data["sha256"].lower():
        return ArchiveProblem(object_id, "original file SHA-256 does not match metadata")
    relative_path = (Path("objects") / object_id / data["original_filename"]).as_posix()
    return ArchiveObject(data, relative_path)


def scan_archive(archive: str | Path) -> ScanReport:
    """Validate UUID object directories without modifying the archive."""
    root = Path(archive)
    if root.is_symlink():
        return ScanReport((), (ArchiveProblem("archive", "archive root is a symbolic link"),))
    objects_dir = root / "objects"
    if objects_dir.is_symlink():
        return ScanReport((), (ArchiveProblem("objects", "objects directory is a symbolic link"),))
    if not objects_dir.exists():
        return ScanReport((), ())
    if not objects_dir.is_dir():
        return ScanReport((), (ArchiveProblem("objects", "objects path is not a directory"),))

    valid: list[ArchiveObject] = []
    problems: list[ArchiveProblem] = []
    try:
        entries = sorted(objects_dir.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        return ScanReport((), (ArchiveProblem("objects", f"cannot scan objects directory: {error}"),))

    for entry in entries:
        if entry.name.startswith(".incomplete-"):
            problems.append(ArchiveProblem(entry.name, "abandoned importer temporary directory; investigate manually"))
            continue
        try:
            parsed_id = uuid.UUID(entry.name)
        except ValueError:
            continue
        if entry.name != str(parsed_id):
            problems.append(ArchiveProblem(entry.name, "object directory name must use canonical lowercase UUID syntax"))
            continue
        result = _scan_object(entry)
        if isinstance(result, ArchiveProblem):
            problems.append(result)
        else:
            valid.append(result)
    return ScanReport(tuple(valid), tuple(problems))


def rebuild_index(archive: str | Path) -> ScanReport:
    """Rebuild the disposable JSON index, refusing replacement on bad objects."""
    root = Path(archive)
    report = scan_archive(root)
    if report.problems:
        return report
    if root.is_symlink():
        raise ArchiveError("archive root must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    if index_path.is_symlink():
        raise ArchiveError("index.json is a symbolic link; refusing to replace it")
    payload = {
        "version": 1,
        "objects": [
            {"metadata": obj.metadata, "path": obj.relative_path}
            for obj in report.objects
        ],
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=".index-incomplete-",
            suffix=".tmp", dir=root, delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, index_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return report
