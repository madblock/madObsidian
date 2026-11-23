"""Persistent filename exclusions for the vault importer."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from madobsidian.locking import process_lock


class ExclusionsError(OSError):
    """Raised when exclusions.ini is malformed or cannot be updated safely."""


_KEY = re.compile(r"^[0-9a-f]{64}$")


def exclusion_path(config_path: Path | None, vault_root: Path) -> Path:
    """Resolve exclusions.ini beside the selected config, or beside the vault."""
    directory = config_path.parent if config_path is not None else vault_root.parent
    return directory / "exclusions.ini"


def filename_key(filename: str) -> str:
    try:
        encoded = filename.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ExclusionsError("Filename cannot be represented as UTF-8 for exclusions.ini") from error
    return hashlib.sha256(encoded).hexdigest()


def _empty_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    return parser


def _read_parser(path: Path) -> tuple[configparser.ConfigParser, bytes | None]:
    if path.is_symlink():
        raise ExclusionsError(f"Exclusions file is a symbolic link: {path}")
    parser = _empty_parser()
    try:
        snapshot = path.read_bytes() if path.exists() else None
        if snapshot is not None:
            parser.read_string(snapshot.decode("utf-8-sig"), source=str(path))
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ExclusionsError(f"Cannot read exclusions file {path}: {error}") from error
    if parser.defaults():
        raise ExclusionsError(f"Malformed exclusions file {path}: DEFAULT entries are not supported")
    unknown = set(parser.sections()) - {"ignore"}
    if unknown:
        raise ExclusionsError(f"Malformed exclusions file {path}: unsupported section(s): {', '.join(sorted(unknown))}")
    if not parser.has_section("ignore"):
        return parser, snapshot
    for key, raw_value in parser.items("ignore", raw=True):
        if _KEY.fullmatch(key) is None:
            raise ExclusionsError(f"Malformed exclusions file {path}: invalid filename hash key {key!r}")
        try:
            filename = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError) as error:
            raise ExclusionsError(f"Malformed exclusions file {path}: value for {key} is not a JSON string") from error
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise ExclusionsError(f"Malformed exclusions file {path}: value for {key} must be a filename JSON string")
        if filename_key(filename) != key:
            raise ExclusionsError(f"Malformed exclusions file {path}: filename hash does not match value for {key}")
    return parser, snapshot


def load_exclusions(path: Path) -> dict[str, str]:
    """Read and validate exclusions; keys are SHA-256 hashes of UTF-8 filenames."""
    parser, _snapshot = _read_parser(path)
    if not parser.has_section("ignore"):
        return {}
    return {key: json.loads(value) for key, value in parser.items("ignore", raw=True)}


def append_exclusion(path: Path, filename: str) -> bool:
    """Atomically append one exclusion under a cross-process update lock."""
    path = Path(path)
    key = filename_key(filename)
    lock_path = path.with_name(path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with process_lock(lock_path):
            # Always reload under the update lock so concurrent writers retain each other's entries.
            parser, snapshot = _read_parser(path)
            if not parser.has_section("ignore"):
                parser.add_section("ignore")
            if parser.has_option("ignore", key):
                return False
            parser.set("ignore", key, json.dumps(filename, ensure_ascii=False))
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", prefix=".exclusions-",
                    suffix=".tmp", dir=path.parent, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    parser.write(stream, space_around_delimiters=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.is_symlink():
                    raise ExclusionsError(f"Exclusions file became a symbolic link: {path}")
                current = path.read_bytes() if path.exists() else None
                if current != snapshot:
                    raise ExclusionsError(f"Exclusions file changed concurrently; refusing to overwrite: {path}")
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
