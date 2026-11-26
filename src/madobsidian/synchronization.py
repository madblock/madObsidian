"""Recognize visible Syncthing conflict and partial-transfer artifacts."""

import os
from collections.abc import Callable
from pathlib import Path


def conflict_copy(name: str) -> bool:
    return ".sync-conflict-" in name.casefold()


def _unfinished_transfer(name: str) -> bool:
    name = name.casefold()
    return name.startswith((".syncthing.", "~syncthing~")) and name.endswith(".tmp")


def sync_artifact(name: str) -> bool:
    return conflict_copy(name) or _unfinished_transfer(name)


def check_sync_state(root: Path, incoming: Path, *, managed_conflict: Callable[[Path], bool] | None = None) -> None:
    def inaccessible(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, onerror=inaccessible, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if _unfinished_transfer(name):
                if path.parent.is_relative_to(incoming) and not conflict_copy(name):
                    continue
            elif conflict_copy(name):
                if managed_conflict is not None and not managed_conflict(path):
                    continue
            else:
                continue
            raise OSError(f"Syncthing conflict or unfinished transfer requires attention: {path}")
