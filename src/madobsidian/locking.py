"""Cross-platform process locks used by vault imports and exclusion updates."""

from __future__ import annotations

import errno
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Acquire a persistent byte/file lock; the lock file is never removed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EDEADLK, errno.EAGAIN} and getattr(error, "winerror", None) not in {33, 36}:
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def vault_lock_path(vault: str | Path) -> Path:
    """Return a stable lock path in the resolved vault's parent directory."""
    root = Path(vault).resolve()
    identity = os.path.normcase(str(root)).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return root.parent / f".madobsidian-vault-{digest}.lock"


@contextmanager
def vault_import_lock(vault: str | Path) -> Iterator[None]:
    """Serialize all import work for one resolved vault."""
    with process_lock(vault_lock_path(vault)):
        yield
