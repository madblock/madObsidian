"""Shared cross-platform validation for archived original filenames."""

import base64
import hashlib
import re
import unicodedata
from pathlib import Path, PureWindowsPath


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\ud800-\udfff]')
_DEVICE = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])$", re.I)


def portable_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).rstrip(" .").casefold()


def filename_problem(filename: str) -> str | None:
    """Return why a name is unsafe for an object's original file, if any."""
    if not filename or filename in {".", ".."}:
        return "original_filename must be a non-empty filename"
    if "/" in filename or "\\" in filename:
        return "original_filename must be a filename without path separators"
    if Path(filename).name != filename or PureWindowsPath(filename).name != filename:
        return "original_filename must not contain a path"
    if Path(filename).is_absolute() or PureWindowsPath(filename).is_absolute() or PureWindowsPath(filename).drive:
        return "original_filename must not be an absolute or drive-qualified path"
    if filename.casefold() == "metadata.json":
        return "original_filename conflicts with the reserved metadata.json filename"
    if (_FORBIDDEN.search(filename) or filename.endswith((".", " "))
            or _DEVICE.fullmatch(filename.partition(".")[0].rstrip())
            or len(filename.encode("utf-8")) > 255):
        return "original_filename contains a nonportable component"
    return None


def storage_name(relative: str, occupants: list[str]) -> str:
    """Select the portable leaf used by the transaction's move into staging."""
    components = relative.split("/")
    name = components[-1]
    occupied = {portable_key(item.rsplit("/", 1)[-1]) for item in occupants if item != relative}
    prefixes = {"/".join(components[:i]) for i in range(1, len(components))}
    folded = {portable_key(prefix) for prefix in prefixes}
    parent_collision = any(prefix not in prefixes and portable_key(prefix) in folded
                           for item in occupants for prefix in _parents(item))
    if (not any(filename_problem(part) for part in components) and portable_key(name) not in occupied
            and not parent_collision):
        return name
    for candidate in portable_candidates(relative):
        if portable_key(candidate) not in occupied:
            return candidate
    raise ValueError(f"No unique portable filename for {relative}")


def portable_candidates(relative: str):
    """Enumerate the names a journal may legitimately record for this source."""
    name = relative.rsplit("/", 1)[-1]
    trimmed = Path(name.rstrip(" ."))
    stem, extension = trimmed.stem, trimmed.suffix
    stem = _FORBIDDEN.sub("_", unicodedata.normalize("NFC", stem)).strip(" .") or "media"
    if _DEVICE.fullmatch(stem.partition(".")[0]):
        stem = "_" + stem
    digest = base64.b32encode(hashlib.sha256(relative.encode("utf-8")).digest()).decode().strip("=")
    for length in range(6, len(digest) + 1):
        suffix = "_~" + digest[:length] + extension
        prefix = stem.encode("utf-8")[:255 - len(suffix.encode("utf-8"))].decode("utf-8", "ignore")
        candidate = prefix + suffix
        if not filename_problem(candidate):
            yield candidate


def _parents(path: str):
    components = path.split("/")
    for length in range(1, len(components)):
        yield "/".join(components[:length])
