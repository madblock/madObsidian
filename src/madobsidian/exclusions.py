"""Persistent filename exclusions for the vault importer."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath

from madobsidian.locking import process_lock, vault_lock_path


class ExclusionsError(OSError):
    """Raised when the vault exclusions file is malformed or cannot be updated safely."""


_KEY = re.compile(r"^[0-9a-f]{64}$")


def exclusion_path(vault_root: Path) -> Path:
    return vault_root / "madobsidian_exclusions.ini"


def filename_key(filename: str) -> str:
    if (PureWindowsPath(filename).is_absolute() or "\\" in filename
            or any(part in {"", ".", ".."} for part in filename.split("/"))):
        raise ExclusionsError(f"Invalid Incoming-relative exclusion path: {filename!r}")
    try:
        encoded = filename.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ExclusionsError("Filename cannot be represented as UTF-8 for the vault exclusions file") from error
    return hashlib.sha256(encoded).hexdigest()


def _empty_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    return parser


def _read_parser(path: Path) -> tuple[configparser.ConfigParser, bytes | None]:
    if path.is_symlink():
        raise ExclusionsError(f"Exclusions file is a symbolic link: {path}")
    conflicts = sorted(path.parent.glob(f"{path.stem}.sync-conflict-*{path.suffix}"))
    if conflicts:
        raise ExclusionsError(f"Syncthing conflict requires manual resolution: {conflicts[0]}")
    parser = _empty_parser()
    try:
        snapshot = path.read_bytes() if path.exists() else None
        if snapshot is not None:
            parser.read_string(snapshot.decode("utf-8-sig"), source=str(path))
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ExclusionsError(f"Cannot read exclusions file {path}: {error}") from error
    if parser.defaults():
        raise ExclusionsError(f"Malformed exclusions file {path}: DEFAULT entries are not supported")
    if not parser.has_section("ignore"):
        return parser, snapshot
    for key, raw_value in parser.items("ignore", raw=True):
        if _KEY.fullmatch(key) is None:
            raise ExclusionsError(f"Malformed exclusions file {path}: invalid filename hash key {key!r}")
        try:
            entry = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError) as error:
            raise ExclusionsError(f"Malformed exclusions file {path}: value for {key} is not a JSON string") from error
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(field), str) and entry[field] for field in ("path", "reason", "message")
        ):
            raise ExclusionsError(f"Invalid exclusion record in {path}: {key}")
        filename = entry["path"]
        if filename.startswith("/") or any(part in {"", ".", ".."} for part in filename.split("/")):
            raise ExclusionsError(f"Malformed exclusions file {path}: value for {key} must be a filename JSON string")
        if filename_key(filename) != key:
            raise ExclusionsError(f"Malformed exclusions file {path}: filename hash does not match value for {key}")
    return parser, snapshot


def load_exclusions(path: Path) -> dict[str, str]:
    """Read and validate exclusions; keys are SHA-256 hashes of UTF-8 filenames."""
    parser, _snapshot = _read_parser(path)
    if not parser.has_section("ignore"):
        return {}
    return {key: json.loads(value)["path"] for key, value in parser.items("ignore", raw=True)}


def append_exclusion(path: Path, filename: str, reason: str = "import_failure",
                     message: str = "Import failed") -> bool:
    """Atomically append one exclusion under a cross-process update lock."""
    path = Path(path)
    key = filename_key(filename)
    lock_path = vault_lock_path(path.parent).with_suffix(".exclusions.lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with process_lock(lock_path):
            # Always reload under the update lock so concurrent writers retain each other's entries.
            parser, snapshot = _read_parser(path)
            if not parser.has_section("ignore"):
                parser.add_section("ignore")
            if parser.has_option("ignore", key):
                return False
            record = json.dumps({"path": filename, "reason": reason, "message": message}, ensure_ascii=False)
            contents = (snapshot or b"").decode("utf-8")
            newline = "\r\n" if "\r\n" in contents else "\n"
            lines = contents.splitlines(keepends=True)
            header = next((i for i, line in enumerate(lines)
                           if (match := parser.SECTCRE.match(line.lstrip("\ufeff").strip()))
                           and match["header"] == "ignore"), None)
            entry = f"{key} = {record}{newline}"
            if header is None:
                contents += (newline if contents and not contents.endswith("\n") else "") + "[ignore]" + newline + entry
            else:
                if not lines[header].endswith("\n"):
                    lines[header] += newline
                lines.insert(header + 1, entry)
                contents = "".join(lines)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", prefix=".exclusions-",
                    suffix=".tmp", dir=path.parent, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.is_symlink():
                    raise ExclusionsError(f"Exclusions file became a symbolic link: {path}")
                current = path.read_bytes() if path.exists() else None
                if current != snapshot:
                    raise ExclusionsError(f"Exclusions file changed concurrently; refusing to overwrite: {path}")
                if any(path.parent.glob(f"{path.stem}.sync-conflict-*{path.suffix}")):
                    raise ExclusionsError(f"Syncthing conflict detected while updating {path}")
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
            return True
    except ExclusionsError:
        raise
    except OSError as error:
        raise ExclusionsError(f"Cannot safely update exclusions file {path}: {error}") from error
