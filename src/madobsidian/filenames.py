"""Shared cross-platform validation for archived original filenames."""

from pathlib import Path, PureWindowsPath


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
    return None
