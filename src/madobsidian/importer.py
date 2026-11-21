"""Import files into a local madObsidian archive."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import uuid
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from madobsidian.config import AppConfig, DEFAULT_CONFIG, resolve_vault_directory
from madobsidian.filenames import filename_problem
from madobsidian.previews import generate_preview
from madobsidian.video_previews import generate_video_preview, is_video_candidate


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


class ImportError(Exception):
    """Raised when an item cannot be safely imported."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(source: Path, identifier: uuid.UUID, digest: str, size: int, *, layout: bool = False,
              threshold: int = DEFAULT_CONFIG.general.small_file_threshold_bytes) -> ImportMetadata:
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
        original_path=(("shared/" if size < threshold else "originals/") + source.name) if layout else source.name,
    )


def _append_import_error(log_path: Path, source: Path | None, error: Exception) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "timestamp": timestamp,
        "level": "ERROR",
        "source": str(source) if source is not None else None,
        "description": " ".join(str(error).split())[:400],
    }
    try:
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never replace the original import failure.
        pass


def _import_file_impl(source: Path, objects: Path, *, layout: bool = False, identifier: uuid.UUID | None = None,
                      transaction: dict[str, object] | None = None, config: AppConfig | None = None) -> ImportResult:
    config = config or DEFAULT_CONFIG
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
    if transaction is not None and (
        transaction.get("sha256") not in (None, source_hash)
        or transaction.get("size_bytes") not in (None, source_size)
    ):
        raise ImportError(f"Source changed while the import was being planned: {source}")
    objects.mkdir(parents=True, exist_ok=True)

    while True:
        identifier = identifier or uuid.uuid4()
        final_dir = objects / str(identifier)
        if final_dir.exists():
            identifier = None
            continue
        temporary_dir = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=objects))
        move_attempted = False
        safe_cleanup_after_move_failure = False
        try:
            relative_original = (("shared" if source_size < config.general.small_file_threshold_bytes else "originals") if layout else "")
            target = temporary_dir / relative_original / source.name
            if relative_original:
                target.parent.mkdir()
            move_source = transaction is not None and layout
            if move_source:
                transaction.update({"id": str(identifier), "sha256": source_hash, "size_bytes": source_size,
                                    "original_path": (relative_original + "/" if relative_original else "") + source.name,
                                    "source_mode": "move", "phase": "moving"})
                _write_journal(temporary_dir / "transaction.json", transaction)
                move_attempted = True
                try:
                    source.rename(target)
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
            else:
                shutil.copyfile(source, target)
            if _sha256(target) != source_hash:
                raise ImportError(f"Object original failed SHA-256 verification: {target}")
            image_preview = generate_preview(
                target,
                temporary_dir / "previews",
                source.name,
                source_size,
                image_config=config.images,
                watermark_config=config.watermark,
                small_file_threshold_bytes=config.general.small_file_threshold_bytes,
            )
            if image_preview is None:
                video_preview = generate_video_preview(
                    target,
                    temporary_dir / "previews",
                    source.name,
                    source_size,
                    config=config,
                )
                if is_video_candidate(source.name, source_size, config.general.small_file_threshold_bytes) and video_preview is None:
                    raise ImportError(f"Eligible video did not produce a preview: {source.name}")
            if move_source and (target.stat().st_size != source_size or _sha256(target) != source_hash):
                raise ImportError(f"Moved original changed during preview processing; preserved at {target}")
            metadata = _metadata(source, identifier, source_hash, source_size, layout=layout,
                                 threshold=config.general.small_file_threshold_bytes)
            with (temporary_dir / "metadata.json").open("x", encoding="utf-8") as stream:
                json.dump(asdict(metadata), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if transaction is not None:
                transaction["phase"] = "published"
                _write_journal(temporary_dir / "transaction.json", transaction)
            try:
                temporary_dir.rename(final_dir)
            except FileExistsError:
                if move_source:
                    raise ImportError(f"Object UUID appeared during publication; original preserved at {target}")
                shutil.rmtree(temporary_dir)
                identifier = None
                continue
            return ImportResult(metadata, final_dir / metadata.original_path)
        except Exception:
            if temporary_dir.exists() and (not move_attempted or safe_cleanup_after_move_failure):
                shutil.rmtree(temporary_dir, ignore_errors=True)
            raise


def _import_file(source: Path, objects: Path, *, layout: bool = False, identifier: uuid.UUID | None = None,
                 transaction: dict[str, object] | None = None, config: AppConfig | None = None,
                 error_log_path: Path | None = None) -> ImportResult:
    try:
        return _import_file_impl(source, objects, layout=layout, identifier=identifier,
                                 transaction=transaction, config=config)
    except Exception as error:
        if error_log_path is not None:
            _append_import_error(error_log_path, source, error)
        raise


def import_path(source: str | Path, archive: str | Path, *, config: AppConfig | None = None) -> list[ImportResult]:
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

    config = config or DEFAULT_CONFIG
    objects = resolve_vault_directory(archive, config.general.objects_directory)
    results: list[ImportResult] = []
    for path in files:
        results.append(_import_file(path, objects, config=config))
    return results


_WIKILINK = re.compile(r"(?P<embed>!)?\[\[(?P<body>.*?)\]\]")


def _write_journal(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _protected_code_ranges(text: str) -> list[tuple[int, int]]:
    """Locate fenced blocks and inline code spans so link examples stay literal."""
    protected: list[tuple[int, int]] = []
    offset = 0
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        if fence_char is not None:
            protected.append((offset, offset + len(line)))
            close = re.match(r"^ {0,3}([`~]+)", line)
            if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_length:
                fence_char, fence_length = None, 0
        else:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening:
                fence_char, fence_length = opening.group(1)[0], len(opening.group(1))
                protected.append((offset, offset + len(line)))
        offset += len(line)
    if fence_char is not None and not text.endswith(("\n", "\r")):
        protected.append((offset, len(text)))

    tick_runs = list(re.finditer(r"(?<!`)(`+)(?!`)", text))
    i = 0
    while i < len(tick_runs):
        opener = tick_runs[i]
        run = opener.group(1)
        closer = next((m for m in tick_runs[i + 1:] if m.group(1) == run), None)
        if closer is None:
            i += 1
            continue
        protected.append((opener.start(), closer.end()))
        i = tick_runs.index(closer) + 1
    return sorted(protected)


def _active_wikilinks(text: str) -> list[re.Match[str]]:
    protected = _protected_code_ranges(text)
    return [m for m in _WIKILINK.finditer(text)
            if not any(start < m.end() and m.start() < end for start, end in protected)]


def _link_replacement(match: re.Match[str], replacement: str, identifier: str, large: bool) -> str:
    body = match.group("body")
    target_and_heading, separator, alias_text = body.partition("|")
    _, marker, heading = target_and_heading.partition("#")
    tail = ("#" + heading) if marker else ""
    alias = ("|" + alias_text) if separator else ""
    embed = match.group("embed") or ""
    linked = f"{embed}[[{replacement}{tail}{alias}]]"
    return linked + (f"\n\n[original](madobsidian://{identifier})" if large else "")


def _markdown_plan(
    vault: Path,
    filename: str,
    identifier: str,
    is_large: bool,
    *,
    eligible_names: list[str] | None = None,
    ambiguities: list[dict[str, object]] | None = None,
    small_file_threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes,
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
        normalized_names.setdefault(unicodedata.normalize("NFC", name), []).append(name)
    from madobsidian.video_previews import is_video_candidate
    video = is_large and is_video_candidate(filename, small_file_threshold_bytes)
    preview = "preview.mp4" if video else "preview.webp"
    replacement = f"{objects_directory}/{identifier}/previews/{preview}" if is_large else f"{objects_directory}/{identifier}/shared/{filename}"
    for note in sorted(vault.rglob("*.md"), key=lambda p: str(p).casefold()):
        if note.is_symlink():
            continue
        with note.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        counts: dict[tuple[str, str], int] = {}
        for match in _active_wikilinks(text):
            target_part = match.group("body").partition("|")[0].partition("#")[0].strip()
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
                            ambiguities.append({"note": note.relative_to(vault).as_posix(), "reference": match.group(0), "candidates": possible})
                # Explicit paths outside Incoming retain the existing exact-path behavior: no match.
            else:
                possible = normalized_names.get(normalized_target, [])
                if len(possible) == 1:
                    selected = possible[0] == filename
                elif len(possible) > 1 and ambiguities is not None:
                    reporter = sorted(possible, key=lambda n: (unicodedata.normalize("NFC", n), n))[0]
                    if filename == reporter:
                        ambiguities.append({"note": note.relative_to(vault).as_posix(), "reference": match.group(0), "candidates": possible})
            if not selected:
                continue
            old_link = match.group(0)
            new_link = _link_replacement(match, replacement, identifier, is_large)
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
    updated = text
    for edit in edits:
        old, new, count = edit["old"], edit["new"], edit["count"]
        if not isinstance(old, str) or not isinstance(new, str) or isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ImportError(f"Malformed Markdown edit in transaction for {note}")
        old_count, new_count = updated.count(old), updated.count(new)
        if old_count + new_count != count:
            raise ImportError(f"Markdown recovery conflict in {note}; note was changed after transaction planning")
        updated = updated.replace(old, new)
    return updated


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".madobsidian-tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_relative(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
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


def _read_transaction(manifest: Path, vault: Path, object_dir: Path, incoming_directory: str,
                      threshold: int) -> dict[str, object]:
    if manifest.is_symlink() or not manifest.is_file():
        raise ImportError(f"Transaction journal is missing, dangling, or unsafe: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImportError(f"Cannot read recovery journal {manifest}: {error}") from error
    required_fields = {"id", "source", "original_path", "sha256", "size_bytes", "markdown", "phase", "source_mode"}
    if not isinstance(data, dict) or set(data) != required_fields or data.get("source_mode") != "move":
        raise ImportError(f"Malformed transaction journal: {manifest}")
    try:
        parsed_id = uuid.UUID(data["id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ImportError("Transaction journal has an invalid UUID") from error
    if parsed_id.version != 4 or str(parsed_id) != data["id"] or object_dir.name != str(parsed_id) and not object_dir.name.startswith(".incomplete-"):
        raise ImportError("Transaction journal UUID does not match its object")
    if not isinstance(data["phase"], str) or data["phase"] not in {"moving", "moved", "published"}:
        raise ImportError("Transaction journal has an invalid phase")
    if (not isinstance(data["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"])
            or isinstance(data["size_bytes"], bool) or not isinstance(data["size_bytes"], int)
            or data["size_bytes"] < 0):
        raise ImportError("Transaction journal has invalid source integrity fields")
    source = _safe_relative(vault, data["source"], label="source")
    if Path(data["source"]).parent.as_posix() != incoming_directory:
        raise ImportError(
            "Transaction was created with a different incoming_directory; rerun with the configuration used to start it"
        )
    filename = source.name
    if filename_problem(filename):
        raise ImportError("Transaction journal has an invalid source filename")
    original_path = data["original_path"]
    expected_folder = "shared" if data["size_bytes"] < threshold else "originals"
    if original_path != f"{expected_folder}/{filename}":
        raise ImportError(
            "Transaction storage layout does not match the configured size threshold; "
            "rerun with the configuration used to start it"
        )
    _safe_relative(object_dir, original_path, label="original")
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
    return data


def _check_integrity(path: Path, data: dict[str, object], *, missing_ok: bool = False) -> bool:
    if path.is_symlink():
        raise ImportError(f"Transaction source or original is a symbolic link: {path}")
    if not path.exists():
        if missing_ok:
            return False
        raise ImportError(f"Transaction file is missing: {path}")
    if not path.is_file() or path.stat().st_size != data["size_bytes"] or _sha256(path) != data["sha256"]:
        raise ImportError(f"Transaction integrity check failed: {path}")
    return True


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
                    shutil.copyfileobj(source_stream, destination_stream)
            except OSError as error:
                raise ImportError(f"Could not restore staged original to Incoming: {source}") from error
            _check_integrity(source, data)
        _check_integrity(source, data)
        target.unlink()


def _cleanup_unjournaled_staging(vault: Path, obj: Path, incoming: Path, *, dry_run: bool) -> dict[str, object]:
    """Only discard a staging directory when it cannot be the sole copy of data."""
    if not any(obj.iterdir()):
        action = "remove empty abandoned staging directory"
        if not dry_run:
            shutil.rmtree(obj)
        return {"id": "unknown", "source": "unknown", "action": action}
    candidates = [p for p in incoming.iterdir() if not p.is_symlink() and p.is_file()]
    staged_originals = [p for folder in ("shared", "originals") for p in (obj / folder).glob("*") if not p.is_symlink() and p.is_file()]
    staged_originals.extend(
        p for p in obj.iterdir()
        if p.is_file() and p.name not in {"metadata.json", "transaction.json.tmp"}
    )
    if len(staged_originals) > 1:
        raise ImportError(f"Cannot safely identify original in abandoned staging directory: {obj}")
    if staged_originals:
        staged = staged_originals[0]
        source = next((p for p in candidates if p.name == staged.name), None)
        if source is None or source.is_symlink() or _sha256(source) != _sha256(staged):
            raise ImportError(f"Abandoned staging data has no verified matching Incoming source: {obj}")
        source_rel = source.relative_to(vault).as_posix()
    else:
        if not candidates:
            raise ImportError(f"Cannot verify source before cleaning abandoned staging directory: {obj}")
        # No original copy exists in this directory, so retain all Incoming data and drop this empty stage.
        source_rel = incoming.relative_to(vault).as_posix().rstrip("/") + "/"
    action = "remove abandoned staging copy after source integrity check"
    if not dry_run:
        shutil.rmtree(obj)
    return {"id": "unknown", "source": source_rel, "action": action}


def _apply_transaction(vault: Path, obj: Path, manifest: Path, data: dict[str, object], *, dry_run: bool = False) -> str:
    target = _safe_relative(obj, data["original_path"], label="original")
    _check_integrity(target, data)
    metadata_path = _safe_relative(obj, "metadata.json", label="metadata")
    if not metadata_path.is_file():
        raise ImportError(f"Published object metadata is missing: {metadata_path}")
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
    planned: list[tuple[Path, str]] = []
    for note_rel, edits in data["markdown"].items():
        note = _safe_relative(vault, note_rel, label="Markdown")
        if not note.is_file():
            raise ImportError(f"Markdown recovery conflict; note is missing: {note}")
        with note.open("r", encoding="utf-8", newline="") as stream:
            original_text = stream.read()
        planned.append((note, _apply_markdown_edits(original_text, edits, note_rel)))
    if dry_run:
        return data["id"]
    for note, updated in planned:
        _atomic_text(note, updated)
    manifest.unlink()
    return data["id"]


def recover_transactions(vault: str | Path, *, dry_run: bool = False,
                         config: AppConfig | None = None) -> tuple[list[dict[str, object]], set[str]]:
    """Recover published imports or safely discard staging copies while source remains valid."""
    config = config or DEFAULT_CONFIG
    root = Path(vault).resolve()
    incoming = resolve_vault_directory(root, config.general.incoming_directory)
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
        if not manifest.exists():
            if obj.name.startswith(".incomplete-"):
                recovery = _cleanup_unjournaled_staging(root, obj, incoming, dry_run=dry_run)
                if (dry_run and isinstance(recovery.get("source"), str)
                        and recovery["source"].startswith(incoming_relative + "/")):
                    covered_sources.add(recovery["source"])
                completed.append(recovery)
                continue
            continue
        data = _read_transaction(manifest, root, obj, incoming_relative,
                                 config.general.small_file_threshold_bytes)
        source = _safe_relative(root, data["source"], label="source")
        if obj.name.startswith(".incomplete-"):
            target = _safe_relative(obj, data["original_path"], label="original")
            source_valid = _check_integrity(source, data, missing_ok=True)
            target_valid = _check_integrity(target, data, missing_ok=True)
            if not source_valid and not target_valid:
                raise ImportError(f"Cannot recover moved original; neither copy verifies: {source}")
            action = ("discard verified staging data and retry Incoming source" if source_valid
                      else "restore verified staged original to Incoming and retry import")
            if dry_run:
                covered_sources.add(data["source"])
            else:
                _restore_moved_staging_original(obj, source, data)
                shutil.rmtree(obj)
                action = "restored verified original to Incoming and discarded unpublished stage"
            completed.append({"id": data["id"], "source": data["source"], "action": action})
        else:
            # Published journals must be UUID directories, and finalization is replayable.
            if obj.name != data["id"] or data["phase"] != "published":
                raise ImportError(f"Published transaction journal has inconsistent state: {manifest}")
            if dry_run:
                _apply_transaction(root, obj, manifest, data, dry_run=True)
                covered_sources.add(data["source"])
                completed.append({"id": data["id"], "source": data["source"], "action": "apply pending Markdown edits and finalize transaction"})
                continue
            identifier = _apply_transaction(root, obj, manifest, data)
            completed.append({"id": identifier, "source": data["source"], "action": "recovered published transaction"})
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


def import_vault(vault: str | Path, *, dry_run: bool = False, config: AppConfig | None = None) -> list[dict[str, object]]:
    """Import regular files from a vault's Incoming directory and rewrite notes."""
    root = Path(vault)
    if root.is_symlink() or not root.is_dir():
        raise ImportError(f"Vault must be a real directory: {root}")
    config = config or DEFAULT_CONFIG
    root = root.resolve()
    incoming = resolve_vault_directory(root, config.general.incoming_directory)
    objects = resolve_vault_directory(root, config.general.objects_directory)
    incoming_relative = incoming.relative_to(root).as_posix()
    objects_relative = objects.relative_to(root).as_posix()
    if incoming == objects or incoming in objects.parents or objects in incoming.parents:
        raise ImportError("Configured Incoming and objects directories must be separate")
    if incoming.is_symlink() or not incoming.is_dir():
        raise ImportError(f"Incoming directory is missing or unsafe: {incoming}")
    error_log_path = root.parent / "import.log"
    try:
        recovery_rows, covered_sources = recover_transactions(root, dry_run=dry_run, config=config)
        _reject_pending_transactions_outside(root, objects)
    except Exception as error:
        if not dry_run:
            _append_import_error(error_log_path, None, error)
        raise
    files = sorted((p for p in incoming.rglob("*") if not p.is_symlink() and p.is_file()), key=lambda p: str(p).casefold())
    direct_files = [p for p in files if p.parent == incoming and p.relative_to(root).as_posix() not in covered_sources]
    for path in direct_files:
        invalid = filename_problem(path.name)
        if invalid:
            if not dry_run:
                _append_import_error(error_log_path, path, ImportError(invalid))
            raise ImportError(invalid)
    eligible_names = [p.name for p in direct_files]
    plan: list[dict[str, object]] = []
    if dry_run:
        for row in recovery_rows:
            plan.append({**row, "target": "", "changes": {}})
    for source in files:
        if source.parent != incoming:
            # Preserve relative attachment paths only when the final object layout supports them;
            # current metadata intentionally permits a basename only.
            continue
        try:
            size = source.stat().st_size
            source_rel = source.relative_to(root).as_posix()
            if source_rel in covered_sources:
                continue
            identifier = str(uuid.uuid4())
            ambiguities: list[dict[str, object]] = []
            edits = _markdown_plan(
                root, source.name, identifier, size >= config.general.small_file_threshold_bytes,
                eligible_names=eligible_names, ambiguities=ambiguities,
                small_file_threshold_bytes=config.general.small_file_threshold_bytes,
                objects_directory=objects_relative,
                incoming_directory=incoming_relative,
            )
            row: dict[str, object] = {"id": identifier, "source": source.relative_to(root).as_posix(),
                                      "target": f"{objects_relative}/{identifier}/{ 'originals' if size >= config.general.small_file_threshold_bytes else 'shared'}/{source.name}",
                                      "preview": ("preview.mp4" if is_video_candidate(source.name, size, config.general.small_file_threshold_bytes) else "preview.webp") if size >= config.general.small_file_threshold_bytes else None,
                                      "changes": edits, "ambiguities": ambiguities, "recovery": False}
            plan.append(row)
            if dry_run:
                continue
            # Move and verify the original, then generate previews and metadata.
            objects.mkdir(parents=True, exist_ok=True)
            manifest: dict[str, object] = {"source": source.relative_to(root).as_posix(),
                                          "markdown": _markdown_plan(
                                              root, source.name, identifier,
                                              size >= config.general.small_file_threshold_bytes,
                                              eligible_names=eligible_names,
                                              small_file_threshold_bytes=config.general.small_file_threshold_bytes,
                                              objects_directory=objects_relative,
                                              incoming_directory=incoming_relative,
                                          ),
                                          "sha256": _sha256(source), "size_bytes": size}
            result = _import_file(source, objects, layout=True, identifier=uuid.UUID(identifier), transaction=manifest,
                                  config=config)
            recover_transactions(root, config=config)
            row["id"] = result.metadata.id
        except Exception as error:
            if not dry_run:
                _append_import_error(error_log_path, source, error)
            raise
    return plan
