"""Validation, verification, and index recovery for local archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from itertools import islice
from typing import Any

from madobsidian.locking import vault_import_lock
from madobsidian.synchronization import sync_artifact
from madobsidian.filenames import filename_problem
from madobsidian.video_previews import _executable, _inspect_video, is_video_candidate
from madobsidian.previews import is_registered_image_filename
from PIL import Image, UnidentifiedImageError

from madobsidian.config import DEFAULT_CONFIG, ToolsConfig

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

    @property
    def complete(self) -> bool:
        """Every observed entry was verified within the resource limits."""
        return not self.problems


class ArchiveError(Exception):
    """Raised when an archive cannot be safely scanned or indexed."""


MAX_METADATA_BYTES = 1024 * 1024
MAX_OBJECTS = 100_000
MAX_SCAN_METADATA_BYTES = 64 * 1024 * 1024

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


def _scan_object(directory: Path, small_file_threshold_bytes: int,
                 tools: ToolsConfig = DEFAULT_CONFIG.tools) -> ArchiveObject | ArchiveProblem:
    object_id = directory.name
    if directory.is_symlink():
        return ArchiveProblem(object_id, "object directory is a symbolic link")
    if not directory.is_dir():
        return ArchiveProblem(object_id, "object entry is not a directory")
    for folder in (directory, directory / "shared", directory / "originals", directory / "previews"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries):
                if count >= 1024:
                    return ArchiveProblem(object_id, "object exceeds scan entry limit")
                if sync_artifact(entry.name):
                    return ArchiveProblem(object_id, f"synchronization incomplete: {entry.name}")
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
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                payload = stream.read(MAX_METADATA_BYTES + 1)
                if len(payload) > MAX_METADATA_BYTES:
                    return ArchiveProblem(object_id, "metadata.json exceeds scan limit")
                data = json.loads(payload.decode("utf-8"))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, ValueError, RecursionError) as error:
        return ArchiveProblem(object_id, f"cannot read metadata.json: {error}")

    validation_error = _validate_metadata(data, object_id)
    if validation_error:
        return ArchiveProblem(object_id, validation_error)
    original_path = data.get("original_path", data["original_filename"])
    if not isinstance(original_path, str) or Path(original_path).is_absolute() or ".." in Path(original_path).parts or "\\" in original_path:
        return ArchiveProblem(object_id, "metadata field 'original_path' must be a safe relative path")
    if len(Path(original_path).parts) not in (1, 2) or (len(Path(original_path).parts) == 2 and Path(original_path).parts[0] not in {"shared", "originals"}):
        return ArchiveProblem(object_id, "metadata field 'original_path' has an unsupported layout")
    if Path(original_path).name != data["original_filename"]:
        return ArchiveProblem(object_id, "metadata field 'original_path' does not match original_filename")
    if len(Path(original_path).parts) == 2:
        expected_folder = "shared" if data["size_bytes"] < small_file_threshold_bytes else "originals"
        if Path(original_path).parts[0] != expected_folder:
            return ArchiveProblem(object_id, "metadata original_path does not match the size-based storage layout")
        if (directory / expected_folder).is_symlink():
            return ArchiveProblem(object_id, "original storage directory is a symbolic link")
    original = directory / original_path
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
    preview_error = _validate_expected_preview(directory, original, data, small_file_threshold_bytes, tools)
    if preview_error:
        return ArchiveProblem(object_id, preview_error)
    previews = directory / "previews"
    if previews.is_symlink():
        return ArchiveProblem(object_id, "previews directory is a symbolic link")
    if previews.exists():
        if not previews.is_dir():
            return ArchiveProblem(object_id, "previews path is not a directory")
        try:
            for preview in previews.iterdir():
                if preview.name not in {"preview.webp", "preview.mp4"}:
                    return ArchiveProblem(object_id, f"unsupported preview file: {preview.name}")
                if preview.is_symlink() or not preview.is_file():
                    return ArchiveProblem(object_id, f"preview is not a regular file: {preview.name}")
        except OSError as error:
            return ArchiveProblem(object_id, f"cannot inspect previews: {error}")
    relative_path = (Path("objects") / object_id / original_path).as_posix()
    return ArchiveObject(data, relative_path)


def _validate_expected_preview(directory: Path, original: Path, metadata: dict[str, Any],
                               small_file_threshold_bytes: int,
                               tools: ToolsConfig = DEFAULT_CONFIG.tools) -> str | None:
    if metadata["size_bytes"] < small_file_threshold_bytes:
        return None
    expected: str | None = None
    try:
        with Image.open(original):
            expected = "preview.webp"
    except UnidentifiedImageError as error:
        if is_registered_image_filename(metadata["original_filename"]):
            return f"eligible image original cannot be decoded: {error}"
    except OSError as error:
        if metadata["mime_type"].startswith("image/"):
            return f"eligible image original cannot be decoded: {error}"
    if expected is None and is_video_candidate(
        metadata["original_filename"], metadata["size_bytes"], small_file_threshold_bytes
    ):
        expected = "preview.mp4"
    if expected is None or ("original_path" not in metadata and not (directory / "previews").exists()):
        return None
    preview = directory / "previews" / expected
    if preview.is_symlink() or not preview.is_file():
        return f"expected preview is missing or unsafe: {expected}"
    if expected == "preview.webp":
        try:
            with Image.open(preview) as image:
                if image.format != "WEBP":
                    return "image preview is not WebP"
                image.verify()
            with Image.open(preview) as image:
                image.load()
        except (OSError, UnidentifiedImageError) as error:
            return f"image preview cannot be decoded: {error}"
        return None
    try:
        ffprobe = _executable("MADOBSIDIAN_FFPROBE", "ffprobe", tools.ffprobe)
        ffmpeg = _executable("MADOBSIDIAN_FFMPEG", "ffmpeg", tools.ffmpeg)
        info = _inspect_video(ffprobe, preview)
        if info is None or info.video.get("codec_name") != "h264":
            return "video preview is not a decodable H.264 video"
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-xerror", "-v", "error", "-i", str(preview), "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=1800,
        )
        if decoded.returncode != 0:
            return f"video preview cannot be decoded (FFmpeg exit {decoded.returncode})"
    except (OSError, ValueError) as error:
        return f"cannot validate video preview: {error}"
    return None


def _resolve_objects_directory(root: Path, configured: str | Path) -> tuple[Path, Path]:
    value = Path(configured).expanduser()
    root_resolved = root.resolve()
    candidate = Path(os.path.abspath(value if value.is_absolute() else root_resolved / value))
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as error:
        raise ArchiveError(f"objects_directory must be inside archive root: {configured}") from error
    current = root_resolved
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ArchiveError(f"objects directory path contains a symbolic link: {current}")
    if not relative.parts:
        raise ArchiveError("objects_directory cannot be the archive root")
    target = candidate.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as error:
        raise ArchiveError(f"objects_directory resolves outside archive root: {configured}") from error
    return target, relative


def scan_archive(archive: str | Path, *, objects_directory: str | Path = "objects",
                 small_file_threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes,
                 tools: ToolsConfig | None = None) -> ScanReport:
    """Validate UUID object directories without modifying the archive."""
    with vault_import_lock(archive):
        return _scan_locked(archive, objects_directory=objects_directory,
                            small_file_threshold_bytes=small_file_threshold_bytes, tools=tools)


def _scan_locked(archive: str | Path, *, objects_directory: str | Path = "objects",
                 small_file_threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes,
                 tools: ToolsConfig | None = None) -> ScanReport:
    resolved_tools = tools or DEFAULT_CONFIG.tools
    root = Path(archive)
    if root.is_symlink():
        return ScanReport((), (ArchiveProblem("archive", "archive root is a symbolic link"),))
    try:
        objects_dir, objects_relative = _resolve_objects_directory(root, objects_directory)
    except ArchiveError as error:
        return ScanReport((), (ArchiveProblem("objects", str(error)),))
    if not objects_dir.exists():
        return ScanReport((), (ArchiveProblem("objects", f"configured objects directory does not exist: {objects_dir}"),))
    if not objects_dir.is_dir():
        return ScanReport((), (ArchiveProblem("objects", "objects path is not a directory"),))

    valid: list[ArchiveObject] = []
    problems: list[ArchiveProblem] = []
    try:
        with os.scandir(root) as root_entries:
            for entry in root_entries:
                if sync_artifact(entry.name):
                    problems.append(ArchiveProblem("archive", f"synchronization incomplete: {entry.name}"))
        with os.scandir(objects_dir) as stream:
            entries = sorted(Path(item.path) for item in islice(stream, MAX_OBJECTS + 1))
        if len(entries) > MAX_OBJECTS:
            return ScanReport((), (ArchiveProblem("objects", "archive exceeds scan entry limit"),))
    except (OSError, MemoryError) as error:
        return ScanReport((), (ArchiveProblem("objects", f"cannot scan objects directory: {error}"),))

    remaining = MAX_SCAN_METADATA_BYTES
    for entry in entries:
        if entry.name.startswith(".incomplete-"):
            problems.append(ArchiveProblem(entry.name, "abandoned importer temporary directory; investigate manually"))
            continue
        journal = entry / "transaction.json"
        if journal.is_symlink():
            problems.append(ArchiveProblem(entry.name, "transaction.json is a symbolic link"))
            continue
        if journal.exists():
            problems.append(ArchiveProblem(entry.name, "unfinished vault import transaction; rerun import to recover"))
            continue
        try:
            metadata = entry / "metadata.json"
            if not entry.is_symlink() and not metadata.is_symlink() and metadata.is_file():
                remaining -= metadata.stat().st_size
                if remaining < 0:
                    problems.append(ArchiveProblem(entry.name, "cumulative metadata scan limit reached"))
                    break
            parsed_id = uuid.UUID(entry.name)
        except OSError as error:
            problems.append(ArchiveProblem(entry.name, f"metadata inspection incomplete: {error}"))
            continue
        except ValueError:
            problems.append(ArchiveProblem(entry.name, "unexpected entry in objects directory"))
            continue
        if entry.name != str(parsed_id):
            problems.append(ArchiveProblem(entry.name, "object directory name must use canonical lowercase UUID syntax"))
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                result = _scan_object(entry, small_file_threshold_bytes, resolved_tools)
        except (OSError, ValueError, MemoryError, Image.DecompressionBombError, Image.DecompressionBombWarning,
                RuntimeError, subprocess.TimeoutExpired) as error:
            result = ArchiveProblem(entry.name, f"verification incomplete ({type(error).__name__}): {error}")
        if isinstance(result, ArchiveProblem):
            problems.append(result)
        else:
            original_relative = result.metadata.get("original_path", result.metadata["original_filename"])
            relative_path = (objects_relative / entry.name / original_relative).as_posix()
            valid.append(ArchiveObject(result.metadata, relative_path))
    index = root / "index.json"
    if not problems and not index.is_symlink() and index.is_file():
        try:
            with index.open("rb") as stream:
                raw = stream.read(MAX_SCAN_METADATA_BYTES + 1)
            if len(raw) > MAX_SCAN_METADATA_BYTES:
                problems.append(ArchiveProblem("index", "index comparison exceeds scan limit"))
            else:
                try:
                    prior = json.loads(raw)
                except (ValueError, UnicodeError):
                    prior = None  # A malformed disposable index can be rebuilt.
                observed = {obj.metadata["id"] for obj in valid}
                if isinstance(prior, dict) and isinstance(prior.get("objects"), list):
                    for item in prior["objects"]:
                        info = item.get("metadata") if isinstance(item, dict) else None
                        if isinstance(info, dict) and isinstance(info.get("id"), str) and info["id"] not in observed:
                            problems.append(ArchiveProblem(info["id"], "indexed object unavailable; check synchronization"))
                            break
        except (OSError, RecursionError, MemoryError) as error:
            problems.append(ArchiveProblem("index", f"index comparison incomplete: {error}"))
    return ScanReport(tuple(valid), tuple(problems))


def rebuild_index(archive: str | Path, *, objects_directory: str | Path = "objects",
                  small_file_threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes,
                  tools: ToolsConfig | None = None) -> ScanReport:
    """Rebuild the disposable JSON index, refusing replacement on bad objects."""
    with vault_import_lock(archive):
        return _rebuild_locked(archive, objects_directory=objects_directory,
                               small_file_threshold_bytes=small_file_threshold_bytes, tools=tools)


def _rebuild_locked(archive: str | Path, *, objects_directory: str | Path = "objects",
                  small_file_threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes,
                  tools: ToolsConfig | None = None) -> ScanReport:
    root = Path(archive)
    index_path = root / "index.json"
    if index_path.is_symlink():
        raise ArchiveError("index.json is a symbolic link")
    snapshot = _hash_file(index_path) if index_path.exists() else None
    report = _scan_locked(root, objects_directory=objects_directory,
                          small_file_threshold_bytes=small_file_threshold_bytes,
                          tools=tools)
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
        current = _hash_file(index_path) if index_path.exists() else None
        if current != snapshot or index_path.is_symlink():
            raise ArchiveError("index.json changed during verification")
        os.replace(temporary_path, index_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return report
