"""Recognize visible Syncthing conflict and partial-transfer artifacts."""

import os
from pathlib import Path


def sync_artifact(name: str) -> bool:
    name = name.casefold()
    return ".sync-conflict-" in name or (
        name.startswith((".syncthing.", "~syncthing~")) and name.endswith(".tmp")
    )


def check_sync_state(root: Path, incoming: Path) -> None:
    def inaccessible(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, onerror=inaccessible, followlinks=False):
        for name in dirs + files:
            if sync_artifact(name):
                if ".sync-conflict-" not in name.casefold() and Path(directory).is_relative_to(incoming):
                    continue
                raise OSError(f"Syncthing conflict or unfinished transfer requires attention: {Path(directory) / name}")
