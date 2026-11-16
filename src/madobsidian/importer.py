"""Import files into a local madObsidian archive."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from madobsidian.filenames import filename_problem


@dataclass(frozen=True)
class ImportMetadata:
    id: str
    original_filename: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    imported_at: str


@dataclass(frozen=True)
class ImportResult:
    metadata: ImportMetadata
    object_path: Path


class ImportError(Exception):
    """Raised when an item cannot be safely imported."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(source: Path, identifier: uuid.UUID, digest: str, size: int) -> ImportMetadata:
    extension = source.suffix
    mime_type, _ = mimetypes.guess_type(source.name)
    return ImportMetadata(
        id=str(identifier),
        original_filename=source.name,
        extension=extension,
        mime_type=mime_type or "application/octet-stream",
        size_bytes=size,
        sha256=digest,
        imported_at=datetime.now(timezone.utc).isoformat(),
    )


def _import_file(source: Path, objects: Path) -> ImportResult:
    # Check the path itself before is_file/open/stat: those operations follow links.
    if source.is_symlink():
        raise ImportError(f"Symbolic links cannot be imported: {source}")
    if not source.is_file():
        raise ImportError(f"Not a regular file: {source}")
    invalid_filename = filename_problem(source.name)
    if invalid_filename:
        raise ImportError(invalid_filename)
    source_hash = _sha256(source)
    source_size = source.stat().st_size
    objects.mkdir(parents=True, exist_ok=True)

    while True:
        identifier = uuid.uuid4()
        final_dir = objects / str(identifier)
        if final_dir.exists():
            continue
        temporary_dir = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=objects))
        try:
            target = temporary_dir / source.name
            shutil.copyfile(source, target)
            if _sha256(target) != source_hash:
                raise ImportError(f"Copied file failed SHA-256 verification: {source}")
            metadata = _metadata(source, identifier, source_hash, source_size)
            with (temporary_dir / "metadata.json").open("x", encoding="utf-8") as stream:
                json.dump(asdict(metadata), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                temporary_dir.rename(final_dir)
            except FileExistsError:
                shutil.rmtree(temporary_dir)
                continue
            return ImportResult(metadata, final_dir / source.name)
        except Exception:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir, ignore_errors=True)
            raise


def import_path(source: str | Path, archive: str | Path) -> list[ImportResult]:
    """Import a file or recursively import regular files from a directory."""
    source_path = Path(source)
    if source_path.is_symlink():
        raise ImportError(f"Symbolic links cannot be imported: {source_path}")
    if source_path.is_file():
        files = [source_path]
    elif source_path.is_dir():
        files = []
        for path in source_path.rglob("*"):
            # Path.is_file follows symlinks, so test and skip links first.
            if path.is_symlink():
                continue
            if path.is_file():
                files.append(path)
        files.sort(key=lambda p: str(p))
    else:
        raise ImportError(f"Source does not exist or is not a file/directory: {source_path}")

    objects = Path(archive) / "objects"
    results: list[ImportResult] = []
    for path in files:
        results.append(_import_file(path, objects))
    return results
