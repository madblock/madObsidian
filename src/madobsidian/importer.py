"""Import files into a local madObsidian archive."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
import os
import shutil
import tempfile
import sys
import uuid
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from madobsidian.config import AppConfig, DEFAULT_CONFIG, resolve_incoming, resolve_vault_directory
from madobsidian.exclusions import ExclusionsError, append_exclusion, exclusion_path, load_exclusions
from madobsidian.filenames import filename_problem, portable_candidates, storage_name
from madobsidian.markdown import references
from madobsidian.synchronization import check_sync_state
from madobsidian.locking import vault_import_lock
from madobsidian.previews import REGISTERED_IMAGE_EXTENSIONS, generate_preview
from madobsidian.video_previews import VIDEO_EXTENSIONS, generate_video_preview, is_video_candidate


@dataclass(frozen=True)
class ImportMetadata:
    id: str
    original_filename: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    imported_at: str
    original_path: str = ""


@dataclass(frozen=True)
class ImportResult:
    metadata: ImportMetadata
    object_path: Path


class ImportBatchResult(list[dict[str, object]]):
    """List-compatible successful/planned rows with explicit batch outcomes."""

    def __init__(self) -> None:
        super().__init__()
        self.recoveries: list[dict[str, object]] = []
        self.skipped: list[dict[str, str]] = []
        self.ignored: list[dict[str, str]] = []
        self.failures: list[dict[str, str | bool]] = []


class ImportError(Exception):
    """Raised when an item cannot be safely imported."""


class IncompleteRecovery(ImportError):
    """Recovery needs a missing file or an operation that could not finish."""


class UnverifiableRecovery(ImportError):
    """Available evidence disagrees with the journal or its safety constraints."""


def _checkpoint(name: str) -> None:
    """Observation point for subprocess crash tests."""


def _sync_directory(directory: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _rename_exclusive(source: Path, destination: Path) -> None:
    if os.name == "nt":
        source.rename(destination)  # Windows rejects an occupied destination.
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        # AT_FDCWD and RENAME_NOREPLACE perform an atomic same-filesystem move.
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError("Filesystem platform lacks a no-overwrite rename primitive")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), destination)


def _media_kind(filename: str) -> str | None:
    suffix = Path(filename.rstrip(" .")).suffix.casefold()
    if suffix in REGISTERED_IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(source: Path, identifier: uuid.UUID, digest: str, size: int, *, threshold: int = DEFAULT_CONFIG.general.small_file_threshold_bytes) -> ImportMetadata:
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
        original_path=(("shared/" if size < threshold else "originals/") + source.name),
    )


def _append_import_error(log_path: Path, source: Path | None, error: Exception, *, level: str = "ERROR") -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "timestamp": timestamp,
        "level": level,
        "source": str(source) if source is not None else None,
        "description": " ".join(str(error).split())[:400],
    }
    try:
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never replace the original import failure.
        pass


def _import_file(vault: Path, source: Path, objects: Path, *, identifier: uuid.UUID,
                 transaction: dict[str, object], archive_name: str, config: AppConfig | None = None) -> ImportResult:
    config = config or DEFAULT_CONFIG
    _safe_relative(vault, transaction["source"], label="source")
    # Check the path itself before is_file/open/stat: those operations follow links.
    if source.is_symlink():
        raise ImportError(f"Symbolic links cannot be imported: {source}")
    if not source.is_file():
        raise ImportError(f"Not a regular file: {source}")
    invalid_filename = filename_problem(archive_name)
    if invalid_filename:
        raise ImportError(invalid_filename)
    source_hash = _sha256(source)
    source_size = source.stat().st_size
    _checkpoint("source_verified")
    if (
        transaction.get("sha256") not in (None, source_hash)
        or transaction.get("size_bytes") not in (None, source_size)
    ):
        raise ImportError(f"Source changed while the import was being planned: {source}")
    objects.mkdir(parents=True, exist_ok=True)

    final_dir = objects / str(identifier)
    if final_dir.exists():
        raise ImportError(f"Planned object identifier is already in use: {identifier}")
    temporary_dir = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=objects))
    _sync_directory(objects)
    _checkpoint("stage_created")
    move_attempted = False
    safe_cleanup_after_move_failure = False
    try:
        relative_original = "shared" if source_size < config.general.small_file_threshold_bytes else "originals"
        target = temporary_dir / relative_original / archive_name
        target.parent.mkdir()
        _checkpoint("layout_created")
        transaction.update({"id": str(identifier), "sha256": source_hash, "size_bytes": source_size,
                            "original_path": relative_original + "/" + archive_name,
                            "source_mode": "move", "phase": "moving"})
        _write_journal(temporary_dir / "transaction.json", transaction)
        move_attempted = True
        try:
            _rename_exclusive(source, target)
            _sync_directory(target.parent)
            _sync_directory(source.parent)
            _checkpoint("source_moved")
        except OSError:
            try:
                source_intact = (
                    not source.is_symlink() and source.is_file()
                    and source.stat().st_size == source_size and _sha256(source) == source_hash
                )
                target_absent = not target.exists() and not target.is_symlink()
                safe_cleanup_after_move_failure = source_intact and target_absent
            except OSError:
                # Any uncertainty means preserve the journal and stage for recovery.
                safe_cleanup_after_move_failure = False
            raise
        if target.stat().st_size != source_size or _sha256(target) != source_hash:
            raise ImportError(f"Moved file failed SHA-256 verification; preserved at {target}")
        transaction["phase"] = "moved"
        _write_journal(temporary_dir / "transaction.json", transaction)
        if _sha256(target) != source_hash:
            raise ImportError(f"Object original failed SHA-256 verification: {target}")
        image_preview = generate_preview(
            target,
            temporary_dir / "previews",
            archive_name,
            source_size,
            image_config=config.images,
            watermark_config=config.watermark,
            small_file_threshold_bytes=config.general.small_file_threshold_bytes,
        )
        if image_preview is None:
            video_preview = generate_video_preview(
                target,
                temporary_dir / "previews",
                archive_name,
                source_size,
                config=config,
            )
            if is_video_candidate(archive_name, source_size, config.general.small_file_threshold_bytes) and video_preview is None:
                raise ImportError(f"Eligible video did not produce a preview: {source.name}")
        if (target.stat().st_size != source_size or _sha256(target) != source_hash):
            raise ImportError(f"Moved original changed during preview processing; preserved at {target}")
        _checkpoint("preview_finished")
        for preview in (temporary_dir / "previews").glob("preview.*"):
            transaction["preview"] = {"path": preview.relative_to(temporary_dir).as_posix(),
                                      "sha256": _sha256(preview), "size_bytes": preview.stat().st_size}
        metadata = _metadata(target, identifier, source_hash, source_size,
                             threshold=config.general.small_file_threshold_bytes)
        record = asdict(metadata)
        if archive_name != source.name:
            record["source_path"] = transaction["source"]
        with (temporary_dir / "metadata.json").open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _checkpoint("metadata_written")
        transaction["phase"] = "prepared"
        _write_journal(temporary_dir / "transaction.json", transaction)
        if archive_name != source.name:
            _apply_transaction(vault, temporary_dir, temporary_dir / "transaction.json", transaction, dry_run=True)
        try:
            _rename_exclusive(temporary_dir, final_dir)
            _sync_directory(objects)
            _checkpoint("object_published")
        except FileExistsError:
            raise ImportError(f"Object UUID appeared during publication; original preserved at {target}")
        return ImportResult(metadata, final_dir / metadata.original_path)
    except Exception:
        if temporary_dir.exists() and (not move_attempted or safe_cleanup_after_move_failure):
            shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

def _write_journal(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise ImportError(f"Unsafe journal update path: {path}")
    snapshot = path.read_bytes() if path.exists() else None
    if snapshot is not None:
        previous = json.loads(snapshot)
        if not isinstance(previous, dict) or any(
            previous.get(key) != data.get(key)
            for key in ("id", "source", "original_path", "sha256", "size_bytes", "markdown", "source_mode")
        ):
            raise ImportError(f"Transaction identity changed: {path}")
    if temporary.exists():
        previous = _load_transaction_json(temporary)
        if any(previous.get(key) != value for key, value in data.items() if key not in {"phase", "failure"}):
            raise ImportError(f"Conflicting pending journal: {temporary}")
        temporary.unlink()
    created = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _checkpoint("journal_temp_" + str(data["phase"]))
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != snapshot:
            raise ImportError(f"Journal changed during update: {path}")
        os.replace(temporary, path)
        created = False
        _sync_directory(path.parent)
        _checkpoint("journal_" + str(data["phase"]))
    finally:
        if created and temporary.exists():
            temporary.unlink()


def _markdown_plan(
    vault: Path,
    filename: str,
    identifier: str,
    is_large: bool,
    *,
    eligible_names: list[str] | None = None,
    ambiguities: list[dict[str, object]] | None = None,
    archive_name: str | None = None,
    objects_directory: str = "objects",
    incoming_directory: str = "Incoming",
) -> dict[str, list[dict[str, object]]]:
    """Plan targeted, counted wikilink replacements keyed by note path."""
    changes: dict[str, list[dict[str, object]]] = {}
    incoming_prefix = incoming_directory.strip("/") + "/"
    normalized_incoming_prefix = unicodedata.normalize("NFC", incoming_prefix)
    names = eligible_names if eligible_names is not None else [filename]
    normalized_names: dict[str, list[str]] = {}
    for name in names:
        normalized_names.setdefault(unicodedata.normalize("NFC", Path(name).name), []).append(name)
    video = is_large and _media_kind(filename) == "video"
    preview = "preview.mp4" if video else "preview.webp"
    replacement = f"{objects_directory}/{identifier}/previews/{preview}" if is_large else f"{objects_directory}/{identifier}/shared/{archive_name or Path(filename).name}"
    for note in sorted(vault.rglob("*.md"), key=lambda p: str(p).casefold()):
        if note.is_symlink():
            continue
        _safe_relative(vault, note.relative_to(vault).as_posix(), label="Markdown")
        with note.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        counts: dict[tuple[str, str], int] = {}
        note_path = note.relative_to(vault).as_posix()
        for match in references(text):
            target_part = match.target
            if match.markdown or target_part.startswith(("./", "../")):
                target_part = posixpath.normpath(posixpath.join(posixpath.dirname(note_path), target_part))
                if not target_part.startswith(incoming_prefix):
                    continue
            normalized_target = unicodedata.normalize("NFC", target_part.replace("\\", "/"))
            selected = False
            if "/" in normalized_target:
                if normalized_target.startswith(normalized_incoming_prefix):
                    possible = [n for n in names if unicodedata.normalize("NFC", f"{incoming_prefix}{n}") == normalized_target]
                    if len(possible) == 1:
                        selected = possible[0] == filename
                    elif len(possible) > 1:
                        raw_exact = [n for n in possible if target_part.replace("\\", "/") == f"{incoming_prefix}{n}"]
                        if len(raw_exact) == 1:
                            selected = raw_exact[0] == filename
                        elif ambiguities is not None and filename == sorted(possible, key=lambda n: (unicodedata.normalize("NFC", n), n))[0]:
                            ambiguities.append({"note": note.relative_to(vault).as_posix(), "reference": match.raw, "candidates": possible})
                # Explicit paths outside Incoming retain the existing exact-path behavior: no match.
            else:
                possible = normalized_names.get(normalized_target, [])
                if len(possible) == 1:
                    selected = possible[0] == filename
                elif len(possible) > 1 and ambiguities is not None:
                    if filename in possible:
                        ambiguities.append({"note": note.relative_to(vault).as_posix(), "reference": match.raw, "candidates": possible})
            if not selected:
                continue
            old_link = match.raw
            new_link = match.render(replacement, note_path)
            if is_large:
                new_link += f"\n\n[original](madobsidian://{identifier})"
            newline = "\r\n" if "\r\n" in text else "\n"
            new_link = new_link.replace("\n", newline)
            counts[(old_link, new_link)] = counts.get((old_link, new_link), 0) + 1
        if counts:
            changes[note.relative_to(vault).as_posix()] = [
                {"old": old, "new": new, "count": count}
                for (old, new), count in counts.items()
            ]
    return changes


def _apply_markdown_edits(text: str, edits: list[dict[str, object]], note: str) -> str:
    active = list(references(text))
    replacements = []
    expected_new: dict[str, int] = {}
    for edit in edits:
        old, new, count = edit["old"], edit["new"], edit["count"]
        if not isinstance(old, str) or not isinstance(new, str) or type(count) is not int or count < 1:
            raise ImportError(f"Malformed Markdown edit in transaction for {note}")
        matches = [ref for ref in active if ref.raw == old]
        if len(matches) > count:
            raise ImportError(f"Markdown recovery conflict in {note}: extra reference")
        replacements.extend((ref.start, ref.end, new) for ref in matches)
        expected_new[new] = expected_new.get(new, 0) + count - len(matches)
    for new, count in expected_new.items():
        if sum(text.startswith(new, ref.start) for ref in active) != count:
            raise ImportError(f"Markdown recovery conflict in {note}; references changed since planning")
    updated = text
    for start, end, new in sorted(replacements, reverse=True):
        updated = updated[:start] + new + updated[end:]
    return updated


def _atomic_text(path: Path, value: str, original: str) -> None:
    temporary = path.with_name(path.name + ".madobsidian-tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ImportError(f"Unsafe Markdown update path: {path}")
    snapshot = original.encode("utf-8")
    if temporary.exists():
        if temporary.read_bytes() != value.encode("utf-8"):
            raise ImportError(f"Conflicting unfinished Markdown update: {temporary}")
        temporary.unlink()
    created = False
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            created = True
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _checkpoint("note_temp_written")
        if path.is_symlink() or path.read_bytes() != snapshot:
            raise ImportError(f"Markdown changed while writing: {path}")
        os.replace(temporary, path)
        created = False
        _sync_directory(path.parent)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def _safe_relative(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or ("\\" in value and (label != "source" or os.name == "nt")):
        raise ImportError(f"Invalid {label} path in transaction journal")
    relative = Path(value)
    if (relative.is_absolute() or relative.as_posix() != value
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise ImportError(f"Unsafe {label} path in transaction journal")
    root_resolved = root.resolve()
    candidate = root / relative
    try:
        candidate.resolve(strict=False).relative_to(root_resolved)
    except (OSError, ValueError) as error:
        raise ImportError(f"{label} path escapes its allowed directory") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ImportError(f"{label} path contains a symbolic link: {current}")
    return candidate


def _load_transaction_json(manifest: Path) -> dict[str, object]:
    if manifest.is_symlink() or not manifest.is_file():
        raise ImportError(f"Transaction journal is missing, dangling, or unsafe: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImportError(f"Cannot read recovery journal {manifest}: {error}") from error
    if not isinstance(data, dict):
        raise ImportError(f"Malformed transaction journal: {manifest}")
    return data


def _read_transaction(manifest: Path, vault: Path, object_dir: Path, incoming_directory: str,
                      threshold: int, *, data: dict[str, object] | None = None) -> dict[str, object]:
    if data is None:
        data = _load_transaction_json(manifest)
    required_fields = {"id", "source", "original_path", "sha256", "size_bytes", "markdown", "phase", "source_mode"}
    if (not isinstance(data, dict) or set(data) - {"failure", "preview"} != required_fields
            or data.get("source_mode") != "move"
            or ("failure" in data and not isinstance(data["failure"], str))):
        raise ImportError(f"Malformed transaction journal: {manifest}")
    try:
        parsed_id = uuid.UUID(data["id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ImportError("Transaction journal has an invalid UUID") from error
    if parsed_id.version != 4 or str(parsed_id) != data["id"] or object_dir.name != str(parsed_id) and not object_dir.name.startswith(".incomplete-"):
        raise ImportError("Transaction journal UUID does not match its object")
    if not isinstance(data["phase"], str) or data["phase"] not in {
        "moving", "moved", "prepared", "published", "completed", "rolling_back", "rolled_back"
    }:
        raise ImportError("Transaction journal has an invalid phase")
    if (not isinstance(data["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"])
            or isinstance(data["size_bytes"], bool) or not isinstance(data["size_bytes"], int)
            or data["size_bytes"] < 0):
        raise ImportError("Transaction journal has invalid source integrity fields")
    source = _safe_relative(vault, data["source"], label="source")
    if not source.is_relative_to(vault / incoming_directory) or source == vault / incoming_directory:
        raise ImportError(
            "Transaction was created with a different incoming_directory; rerun with the configuration used to start it"
        )
    filename = Path(str(data["original_path"])).name
    if filename_problem(filename):
        raise ImportError("Transaction journal has an invalid archive filename")
    identity = source.relative_to(vault / incoming_directory).as_posix()
    if filename != source.name and filename not in portable_candidates(identity):
        raise ImportError("Archive filename does not correspond to the journaled Incoming source")
    original_path = data["original_path"]
    expected_folder = "shared" if data["size_bytes"] < threshold else "originals"
    if original_path != f"{expected_folder}/{filename}":
        raise ImportError(
            "Transaction storage layout does not match the configured size threshold; "
            "rerun with the configuration used to start it"
        )
    _safe_relative(object_dir, original_path, label="original")
    if "preview" in data:
        preview = data["preview"]
        if (not isinstance(preview, dict) or preview.get("path") not in ("previews/preview.webp", "previews/preview.mp4")
                or type(preview.get("size_bytes")) is not int or preview["size_bytes"] < 0
                or not isinstance(preview.get("sha256"), str) or not re.fullmatch("[a-f0-9]{64}", preview["sha256"])):
            raise ImportError(f"Invalid transaction preview descriptor: {manifest}")
    if not isinstance(data["markdown"], dict):
        raise ImportError("Transaction journal Markdown edits must be an object")
    for note_rel, edits in data["markdown"].items():
        note = _safe_relative(vault, note_rel, label="Markdown")
        if note.suffix.casefold() != ".md" or not isinstance(edits, list):
            raise ImportError("Transaction journal contains an invalid Markdown entry")
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {"old", "new", "count"}:
                raise ImportError("Transaction journal contains a malformed Markdown edit")
            if (not isinstance(edit["old"], str) or not isinstance(edit["new"], str)
                    or isinstance(edit["count"], bool) or not isinstance(edit["count"], int)
                    or edit["count"] < 1):
                raise ImportError("Transaction journal contains invalid Markdown edit values")
            links = list(references(edit["old"]))
            if len(links) != 1 or links[0].raw != edit["old"]:
                raise ImportError("Transaction edit does not identify one active attachment reference")
            link = links[0]
            raw_target = link.target.replace("\\", "/")
            if link.markdown or raw_target.startswith(("./", "../")):
                raw_target = posixpath.normpath(posixpath.join(posixpath.dirname(note_rel), raw_target))
            expected = source.name if "/" not in raw_target and not link.markdown else data["source"]
            if unicodedata.normalize("NFC", raw_target) != unicodedata.normalize("NFC", expected):
                raise ImportError("Transaction edit refers to a different Incoming source")
            large = expected_folder == "originals"
            preview = "preview.mp4" if _media_kind(filename) == "video" else "preview.webp"
            target_path = object_dir.parent.relative_to(vault) / data["id"]
            target_path /= f"previews/{preview}" if large else original_path
            replacement = link.render(target_path.as_posix(), note_rel)
            if large:
                replacement += f"\n\n[original](madobsidian://{data['id']})"
            if edit["new"].replace("\r\n", "\n") != replacement:
                raise ImportError("Transaction edit destination does not match its archive object")
    return data


def _check_integrity(path: Path, data: dict[str, object], *, missing_ok: bool = False) -> bool:
    if path.is_symlink():
        raise ImportError(f"Transaction source or original is a symbolic link: {path}")
    if not path.exists():
        if missing_ok:
            return False
        raise IncompleteRecovery(f"Transaction file is missing: {path}")
    if not path.is_file() or path.stat().st_size != data["size_bytes"] or _sha256(path) != data["sha256"]:
        raise ImportError(f"Transaction integrity check failed: {path}")
    return True


def _source_matches(path: Path, size: int, digest: str) -> bool:
    try:
        return (not path.is_symlink() and path.is_file() and path.stat().st_size == size
                and _sha256(path) == digest)
    except OSError:
        return False


def _source_present(path: Path, size: int | None) -> bool:
    try:
        return (not path.is_symlink() and path.is_file()
                and (size is None or path.stat().st_size == size))
    except OSError:
        return False


def _restore_moved_staging_original(obj: Path, source: Path, data: dict[str, object]) -> None:
    """Restore a verified moved original to Incoming before discarding an unpublished stage."""
    target = _safe_relative(obj, data["original_path"], label="original")
    source_valid = _check_integrity(source, data, missing_ok=True)
    target_valid = _check_integrity(target, data, missing_ok=True)
    if not source_valid and not target_valid:
        raise ImportError(f"Neither Incoming nor staging contains the verified original for {source}")
    if target_valid and not source_valid:
        try:
            # A hard link is an atomic, no-overwrite restoration on the same vault filesystem.
            os.link(target, source)
        except FileExistsError as error:
            raise ImportError(f"Incoming path appeared during original restoration: {source}") from error
        except OSError:
            # Some filesystems disallow links; create the destination exclusively and verify it.
            try:
                with target.open("rb") as source_stream, source.open("xb") as destination_stream:
                    _checkpoint("rollback_copy_opened")
                    shutil.copyfileobj(source_stream, destination_stream)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                    _checkpoint("rollback_copy_written")
            except OSError as error:
                raise ImportError(f"Could not restore staged original to Incoming: {source}") from error
            _check_integrity(source, data)
        _check_integrity(source, data)
        _sync_directory(source.parent)
        _checkpoint("source_restored")
        target.unlink()
        _checkpoint("staged_original_removed")


def _validate_stage_contents(obj: Path, original_path: str) -> None:
    """Only clean payload files the importer creates in an unpublished stage."""
    original = Path(original_path)
    for entry in obj.iterdir():
        if entry.is_symlink():
            raise ImportError(f"Unverifiable staging link: {entry}")
        if entry.name in {"metadata.json", "transaction.json", "transaction.json.tmp"} and entry.is_file():
            continue
        if entry.name == original.parent.name:
            allowed = {original.name}
        elif entry.name == "previews":
            allowed = {"preview.webp", "preview.mp4"}
        elif entry.name.startswith(".video-work-"):
            allowed = {"watermark.png", "preview.mp4"}
        else:
            raise ImportError(f"Unexpected staging entry requires inspection: {entry}")
        if not entry.is_dir() or any(child.name not in allowed or child.is_symlink() or not child.is_file()
                                     for child in entry.iterdir()):
            raise ImportError(f"Unexpected staging contents require inspection: {entry}")


def _cleanup_unjournaled_staging(vault: Path, obj: Path, incoming: Path, *, dry_run: bool) -> dict[str, object]:
    """Only discard a staging directory when it cannot be the sole copy of data."""
    if not any(obj.iterdir()):
        action = "remove empty abandoned staging directory"
        if not dry_run:
            shutil.rmtree(obj)
        return {"id": "unknown", "source": "unknown", "action": action}
    for child in obj.iterdir():
        if child.is_symlink():
            raise ImportError(f"Unverifiable abandoned stage contains a symbolic link: {child}")
        if child.is_dir():
            if child.name not in {"shared", "originals"} or any(
                item.is_symlink() or not item.is_file() for item in child.iterdir()
            ):
                raise ImportError(f"Unverifiable abandoned stage contents: {child}")
        elif child.name == "metadata.json":
            raise ImportError(f"Abandoned stage contains metadata without a journal: {obj}")
    staged_originals = [p for folder in ("shared", "originals") for p in (obj / folder).glob("*") if not p.is_symlink() and p.is_file()]
    staged_originals.extend(
        p for p in obj.iterdir()
        if p.is_file() and p.name not in {"metadata.json", "transaction.json.tmp"}
    )
    if len(staged_originals) > 1:
        raise ImportError(f"Cannot safely identify original in abandoned staging directory: {obj}")
    if staged_originals:
        staged = staged_originals[0]
        source = incoming / staged.name
        if not source.is_file() or source.is_symlink() or _sha256(source) != _sha256(staged):
            raise ImportError(f"Abandoned staging data has no verified matching Incoming source: {obj}")
        source_rel = source.relative_to(vault).as_posix()
    else:
        # No original copy exists in this directory, so retain all Incoming data and drop this empty stage.
        source_rel = incoming.relative_to(vault).as_posix().rstrip("/") + "/"
    action = "remove abandoned staging copy after source integrity check"
    if not dry_run:
        shutil.rmtree(obj)
    return {"id": "unknown", "source": source_rel, "action": action}


def _apply_transaction(vault: Path, obj: Path, manifest: Path, data: dict[str, object], *,
                       dry_run: bool = False) -> str:
    target = _safe_relative(obj, data["original_path"], label="original")
    _check_integrity(target, data)
    if "preview" in data:
        preview = data["preview"]
        _check_integrity(_safe_relative(obj, preview["path"], label="preview"), preview)
    metadata_path = _safe_relative(obj, "metadata.json", label="metadata")
    if not metadata_path.is_file():
        raise IncompleteRecovery(f"Published object metadata is missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImportError(f"Published object metadata cannot be read: {metadata_path}") from error
    if (not isinstance(metadata, dict) or metadata.get("id") != data["id"]
            or metadata.get("original_filename") != target.name
            or metadata.get("original_path") != data["original_path"]
            or metadata.get("size_bytes") != data["size_bytes"]
            or metadata.get("sha256") != data["sha256"]):
        raise ImportError(f"Published object metadata does not match its transaction: {metadata_path}")
    planned: list[tuple[Path, str, str]] = []
    for note_rel, edits in data["markdown"].items():
        note = _safe_relative(vault, note_rel, label="Markdown")
        if not note.is_file():
            raise ImportError(f"Markdown recovery conflict; note is missing: {note}")
        with note.open("r", encoding="utf-8", newline="") as stream:
            original_text = stream.read()
        planned.append((note, original_text, _apply_markdown_edits(original_text, edits, note_rel)))
    if dry_run:
        return data["id"]
    if data["phase"] == "prepared":
        data["phase"] = "published"
        _write_journal(manifest, data)
    for note, original, updated in planned:
        with note.open("r", encoding="utf-8", newline="") as stream:
            if stream.read() != original:
                raise ImportError(f"Markdown changed during recovery: {note}")
        if updated != original:
            _atomic_text(note, updated, original)
            _checkpoint("note_updated")
    if data["phase"] != "completed":
        data["phase"] = "completed"
        _write_journal(manifest, data)
    manifest.unlink()
    _sync_directory(obj)
    _checkpoint("journal_removed")
    return data["id"]


def recover_transactions(vault: str | Path, *, dry_run: bool = False,
                         config: AppConfig | None = None,
                         only_source: str | None = None) -> tuple[list[dict[str, object]], set[str]]:
    """Serialize standalone recovery with imports on this vault."""
    with vault_import_lock(vault):
        root = Path(vault).resolve()
        check_sync_state(root, resolve_incoming(root, config or DEFAULT_CONFIG))
        try:
            return _recover_locked(vault, dry_run=dry_run, config=config, only_source=only_source)
        except IncompleteRecovery:
            raise
        except OSError as error:
            raise IncompleteRecovery(f"Incomplete recovery: {error}") from error
        except ImportError as error:
            raise UnverifiableRecovery(f"Unverifiable recovery: {error}") from error


def _recover_locked(vault: str | Path, *, dry_run: bool = False,
                    config: AppConfig | None = None,
                    only_source: str | None = None,
                    failure: str | None = None) -> tuple[list[dict[str, object]], set[str]]:
    config = config or DEFAULT_CONFIG
    root = Path(vault).resolve()
    incoming = resolve_incoming(root, config)
    objects = resolve_vault_directory(root, config.general.objects_directory)
    incoming_relative = incoming.relative_to(root).as_posix()
    completed: list[dict[str, object]] = []
    covered_sources: set[str] = set()
    if objects.is_symlink():
        raise ImportError("objects directory is a symbolic link")
    if not objects.exists():
        return completed, covered_sources
    for obj in sorted(objects.iterdir()):
        if obj.is_symlink():
            if obj.name.startswith(".incomplete-") or _is_uuid_name(obj.name):
                raise ImportError(f"Object entry is a symbolic link: {obj}")
            continue
        if not obj.is_dir():
            continue
        manifest = obj / "transaction.json"
        if manifest.is_symlink():
            raise ImportError(f"Transaction journal is a symbolic link: {manifest}")
        if not manifest.exists() and obj.name.startswith(".incomplete-"):
            pending = obj / "transaction.json.tmp"
            if pending.is_symlink() or pending.exists():
                _read_transaction(pending, root, obj, incoming_relative, config.general.small_file_threshold_bytes)
                if dry_run:
                    manifest = pending
                else:
                    _rename_exclusive(pending, manifest)
                    _sync_directory(obj)
        if not manifest.exists():
            if only_source is not None:
                continue
            if obj.name.startswith(".incomplete-"):
                recovery = _cleanup_unjournaled_staging(root, obj, incoming, dry_run=dry_run)
                if (dry_run and isinstance(recovery.get("source"), str)
                        and recovery["source"].startswith(incoming_relative + "/")):
                    covered_sources.add(recovery["source"])
                completed.append(recovery)
                continue
            continue
        if only_source is None:
            data = _read_transaction(
                manifest, root, obj, incoming_relative,
                config.general.small_file_threshold_bytes,
            )
        else:
            candidate = _load_transaction_json(manifest)
            journal_source = candidate.get("source")
            if not isinstance(journal_source, str):
                raise ImportError(f"Malformed transaction journal: {manifest}")
            if journal_source != only_source:
                continue
            data = _read_transaction(
                manifest, root, obj, incoming_relative,
                config.general.small_file_threshold_bytes,
                data=candidate,
            )
        source = _safe_relative(root, data["source"], label="source")
        if obj.name.startswith(".incomplete-"):
            target = _safe_relative(obj, data["original_path"], label="original")
            _validate_stage_contents(obj, data["original_path"])
            source_valid = _check_integrity(source, data, missing_ok=True)
            target_valid = _check_integrity(target, data, missing_ok=True)
            if not source_valid and not target_valid:
                raise ImportError(f"Cannot recover moved original; neither copy verifies: {source}")
            action = "restore verified source and record its exclusion"
            if dry_run:
                covered_sources.add(data["source"])
            else:
                data.setdefault("failure", failure or "Interrupted import restored to Incoming")
                data["phase"] = "rolling_back"
                _write_journal(manifest, data)
                _restore_moved_staging_original(obj, source, data)
                _check_integrity(source, data)
                data["phase"] = "rolled_back"
                _write_journal(manifest, data)
                try:
                    append_exclusion(exclusion_path(root), source.relative_to(incoming).as_posix(),
                                     message=data["failure"])
                except ExclusionsError as error:
                    raise ImportError(f"Original restored but madobsidian_exclusions.ini update failed: {error}") from error
                _checkpoint("rollback_exclusion_written")
                for child in obj.iterdir():
                    if child != manifest:
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                _checkpoint("stage_payload_removed")
                manifest.unlink()
                _checkpoint("rollback_journal_removed")
                obj.rmdir()
                action = "restored verified original to Incoming and discarded unpublished stage"
            completed.append({"id": data["id"], "source": data["source"], "action": action,
                              "state": "pending" if dry_run else "rolled_back"})
        else:
            # Published journals must be UUID directories, and finalization is replayable.
            if obj.name != data["id"] or data["phase"] not in {"prepared", "published", "completed"}:
                raise ImportError(f"Published transaction journal has inconsistent state: {manifest}")
            if dry_run:
                _apply_transaction(root, obj, manifest, data, dry_run=True)
                covered_sources.add(data["source"])
                completed.append({"id": data["id"], "source": data["source"], "action": "apply pending Markdown edits and finalize transaction"})
                continue
            identifier = _apply_transaction(root, obj, manifest, data)
            completed.append({"id": identifier, "source": data["source"],
                              "action": "recovered published transaction", "state": "completed"})
    return completed, covered_sources


def _is_uuid_name(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _reject_pending_transactions_outside(vault: Path, objects: Path) -> None:
    """Avoid starting a second import when a prior objects_directory has a pending transaction."""
    configured_objects = objects.resolve()

    def walk_error(error: OSError) -> None:
        raise ImportError(f"Cannot check for transactions outside the configured objects directory: {error}") from error

    for current_text, directories, files in os.walk(vault, topdown=True, followlinks=False, onerror=walk_error):
        current = Path(current_text)
        if current == configured_objects or configured_objects in current.parents:
            directories.clear()
            continue
        if current.name.startswith(".incomplete-"):
            raise ImportError(
                f"Unfinished staging directory exists outside the configured objects directory: {current}; "
                "restore the configuration used to start that import"
            )
        if _is_uuid_name(current.name) and "transaction.json" in files:
            raise ImportError(
                f"Pending transaction exists outside the configured objects directory: {current}; "
                "restore the configuration used to start that import"
            )
        retained: list[str] = []
        for name in directories:
            child = current / name
            if child.is_symlink():
                if name.startswith(".incomplete-") or _is_uuid_name(name):
                    raise ImportError(f"Unsafe transaction-like directory outside configured objects: {child}")
                continue
            if name.startswith(".incomplete-"):
                raise ImportError(
                    f"Unfinished staging directory exists outside the configured objects directory: {child}; "
                    "restore the configuration used to start that import"
                )
            retained.append(name)
        directories[:] = retained


def import_vault(vault: str | Path, *, dry_run: bool = False,
                 config: AppConfig | None = None) -> ImportBatchResult:
    root = Path(vault)
    if root.is_symlink() or not root.is_dir():
        raise ImportError(f"Vault must be a real directory: {root}")
    with vault_import_lock(root.resolve()):
        root = root.resolve()
        check_sync_state(root, resolve_incoming(root, config or DEFAULT_CONFIG))
        return _import_vault_locked(root, dry_run=dry_run, config=config)


def _import_vault_locked(vault: str | Path, *, dry_run: bool = False,
                         config: AppConfig | None = None) -> ImportBatchResult:
    """Import regular files from a vault's Incoming directory and rewrite notes."""
    root = Path(vault)
    if root.is_symlink() or not root.is_dir():
        raise ImportError(f"Vault must be a real directory: {root}")
    config = config or DEFAULT_CONFIG
    root = root.resolve()
    incoming = resolve_incoming(root, config)
    objects = resolve_vault_directory(root, config.general.objects_directory)
    incoming_relative = incoming.relative_to(root).as_posix()
    objects_relative = objects.relative_to(root).as_posix()
    if incoming == objects or incoming in objects.parents or objects in incoming.parents:
        raise ImportError("Configured Incoming and objects directories must be separate")
    if not incoming.exists():
        _append_import_error(root.parent / "import.log", incoming,
                             ImportError(f"Incoming directory is missing: {incoming}"), level="WARNING")
    error_log_path = root.parent / "import.log"
    batch = ImportBatchResult()
    try:
        recovery_rows, covered_sources = _recover_locked(root, dry_run=dry_run, config=config)
        _reject_pending_transactions_outside(root, objects)
    except Exception as error:
        if not dry_run:
            _append_import_error(error_log_path, None, error)
        raise
    exclusions_file = exclusion_path(root)
    try:
        exclusions = load_exclusions(exclusions_file)
    except ExclusionsError as error:
        if not dry_run:
            _append_import_error(error_log_path, None, error)
        raise ImportError(str(error)) from error
    excluded_filenames = set(exclusions.values())
    files = sorted((p for p in incoming.rglob("*") if not p.is_symlink() and p.is_file()), key=lambda p: str(p).casefold())
    uncovered_files = [p for p in files if p.relative_to(root).as_posix() not in covered_sources]

    def report_failure(source: Path, relative_path: str, digest: str | None, size: int | None,
                       error: Exception, *, transaction_started: bool = False,
                       already_published: bool = False) -> None:
        reason = " ".join(str(error).split())
        if dry_run:
            batch.failures.append({"source": relative_path, "reason": reason, "serious": False})
            return
        serious = False
        state = "failed"
        if already_published:
            reason = f"Import object was published but transaction finalization failed: {reason}"
            serious = True
            state = "unverifiable" if isinstance(error, ImportError) and not isinstance(error, IncompleteRecovery) else "incomplete"
        else:
            try:
                if transaction_started:
                    recovered, _ = _recover_locked(root, config=config, only_source=relative_path, failure=reason)
                    if any(row.get("state") == "completed" for row in recovered):
                        raise ImportError("Object was already published and finalized during recovery")
                restored = (_source_matches(source, size, digest)
                            if digest is not None and size is not None
                            else not transaction_started and _source_present(source, size))
                if not restored:
                    raise ImportError("original integrity could not be verified at its Incoming path")
            except Exception as recovery_error:
                reason = f"Import failed: {reason}; serious recovery error: {recovery_error}"
                serious = True
                state = "incomplete" if isinstance(recovery_error, (OSError, IncompleteRecovery)) else "unverifiable"
            else:
                state = "rolled_back" if transaction_started else "failed"
                try:
                    append_exclusion(exclusions_file, source.relative_to(incoming).as_posix(), message=reason or type(error).__name__)
                except ExclusionsError as exclusion_error:
                    reason = (
                        f"Import failed; original was restored and verified, but madobsidian_exclusions.ini update failed: "
                        f"{exclusion_error}"
                    )
        _append_import_error(error_log_path, source, ImportError(reason))
        batch.failures.append({"source": relative_path, "reason": reason, "serious": serious, "state": state})

    media_files: list[Path] = []
    file_sizes: dict[Path, int] = {}
    for path in uncovered_files:
        filename = path.relative_to(incoming).as_posix()
        if filename in excluded_filenames:
            batch.ignored.append({
                "source": path.relative_to(incoming).as_posix(),
                "reason": "listed in madobsidian_exclusions.ini",
            })
            continue
        size: int | None = None
        if any(part.startswith(".") for part in path.relative_to(incoming).parts):
            batch.skipped.append({"source": path.relative_to(incoming).as_posix(), "reason": "hidden file"})
            continue
        if _media_kind(path.name) is None:
            batch.skipped.append({"source": path.relative_to(incoming).as_posix(), "reason": "unsupported media extension"})
            continue
        source_rel = path.relative_to(root).as_posix()
        try:
            _safe_relative(incoming, filename, label="source")
            size = path.stat().st_size
        except Exception as error:
            report_failure(path, source_rel, None, size, error)
            continue
        file_sizes[path] = size
        media_files.append(path)

    candidate_names = [p.relative_to(incoming).as_posix() for p in files
                       if _media_kind(p.name) and not any(part.startswith(".") for part in p.relative_to(incoming).parts)]
    candidate_plans: dict[Path, dict[str, object]] = {}
    ambiguous_names: set[str] = set()
    occupants = [p.relative_to(incoming).as_posix() for p in files]
    for source in media_files:
        size = file_sizes[source]
        digest = None
        source_rel = source.relative_to(root).as_posix()
        try:
            identifier = str(uuid.uuid4())
            ambiguities: list[dict[str, object]] = []
            archive_name = storage_name(source.relative_to(incoming).as_posix(), occupants)
            if archive_name != source.name:
                occupants.append(archive_name)
            reference_plan = _markdown_plan(
                root, source.relative_to(incoming).as_posix(), identifier,
                size >= config.general.small_file_threshold_bytes,
                eligible_names=candidate_names,
                ambiguities=ambiguities,
                archive_name=archive_name,
                objects_directory=objects_relative,
                incoming_directory=incoming_relative,
            )
            candidate_plans[source] = {
                "id": identifier,
                "archive_name": archive_name,
                "size": size,
                "changes": reference_plan,
                "ambiguities": ambiguities,
            }
            for ambiguity in ambiguities:
                ambiguous_names.update(ambiguity["candidates"])
        except Exception as error:
            report_failure(source, source_rel, digest, size, error)
            continue
    referenced_files = [
        source for source in media_files
        if source in candidate_plans
        and (candidate_plans[source]["changes"] or source.relative_to(incoming).as_posix() in ambiguous_names)
    ]
    if dry_run:
        for row in recovery_rows:
            batch.recoveries.append(dict(row))
    referenced_set = set(referenced_files)
    for source in media_files:
        if source not in referenced_set and source in candidate_plans:
            batch.skipped.append({"source": source.relative_to(incoming).as_posix(), "reason": "no matching Markdown reference"})
    for source in referenced_files:
        candidate = candidate_plans[source]
        identity = source.relative_to(incoming).as_posix()
        if identity in ambiguous_names:
            others = sorted({name for plan in candidate_plans.values() for issue in plan["ambiguities"]
                             if identity in issue["candidates"] for name in issue["candidates"] if name != identity})
            message = "Ambiguous Markdown reference; conflicts with " + ", ".join(others)
            if not dry_run:
                try:
                    append_exclusion(exclusions_file, identity, "nfc_ambiguity", message)
                except ExclusionsError as error:
                    _append_import_error(error_log_path, source, error)
                    batch.failures.append({"source": identity, "reason": str(error), "serious": True})
                    continue
            batch.skipped.append({"source": identity, "reason": message})
            continue
        size = file_sizes[source]
        digest = None
        source_rel = source.relative_to(root).as_posix()
        transaction_started = False
        published = False
        try:
            if source_rel in covered_sources:
                continue
            try:
                digest = _sha256(source)
            except OSError as error:
                report_failure(source, source_rel, None, size, error)
                continue
            identifier = candidate["id"]
            edits = candidate["changes"]
            row: dict[str, object] = {"id": identifier, "source": source.relative_to(root).as_posix(),
                                      "target": f"{objects_relative}/{identifier}/{ 'originals' if size >= config.general.small_file_threshold_bytes else 'shared'}/{candidate['archive_name']}",
                                      "preview": ("preview.mp4" if _media_kind(source.name) == "video" else "preview.webp") if size >= config.general.small_file_threshold_bytes else None,
                                      "changes": edits, "ambiguities": candidate["ambiguities"], "recovery": False}
            if dry_run:
                batch.append(row)
                continue
            objects.mkdir(parents=True, exist_ok=True)
            manifest: dict[str, object] = {"source": source.relative_to(root).as_posix(),
                                          "markdown": edits,
                                          "sha256": digest, "size_bytes": size}
            transaction_started = True
            result = _import_file(root, source, objects, identifier=uuid.UUID(identifier), transaction=manifest,
                                  archive_name=candidate["archive_name"], config=config)
            published = True
            _recover_locked(root, config=config, only_source=source_rel)
            row["id"] = result.metadata.id
            batch.append(row)
        except Exception as error:
            if dry_run:
                raise
            report_failure(source, source_rel, digest, size, error,
                           transaction_started=transaction_started, already_published=published)
    return batch
