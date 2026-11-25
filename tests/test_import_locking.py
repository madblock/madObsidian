"""Process-level tests for vault import serialization."""

from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from madobsidian.locking import vault_import_lock, vault_lock_path


def _import_worker(vault_text: str, barrier, results) -> None:
    try:
        from madobsidian.importer import import_vault

        barrier.wait(timeout=15)
        batch = import_vault(Path(vault_text))
        results.put(("ok", len(batch), len(batch.failures)))
    except BaseException as error:
        results.put(("error", repr(error)))


def _maintenance_worker(operation, vault, started, finished):
    from madobsidian.archive import rebuild_index, scan_archive
    from madobsidian.importer import recover_transactions
    started.set()
    {"verify": scan_archive, "rebuild": rebuild_index, "recover": recover_transactions}[operation](vault)
    finished.set()


class ImportLockTests(unittest.TestCase):
    def test_maintenance_waits_for_same_vault_lock(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            (vault / "objects").mkdir(parents=True)
            for operation in ("verify", "rebuild", "recover"):
                started, finished = context.Event(), context.Event()
                proc = context.Process(target=_maintenance_worker, args=(operation, vault, started, finished))
                with self.subTest(operation=operation):
                    with vault_import_lock(vault):
                        proc.start()
                        self.assertTrue(started.wait(10))
                        self.assertFalse(finished.wait(0.25))
                    proc.join(10)
                    self.assertEqual(proc.exitcode, 0)
                    self.assertTrue(finished.is_set())

    def test_vault_lock_path_is_stable_hashed_in_parent_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            vault.mkdir()
            first = vault_lock_path(vault)
            second = vault_lock_path(vault / ".")
            self.assertEqual(first, second)
            self.assertEqual(first.parent, vault.resolve().parent)
            self.assertRegex(first.name, r"^\.madobsidian-vault-[0-9a-f]{64}\.lock$")

    def test_concurrent_import_processes_create_only_one_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            vault = base / "vault"
            incoming = vault / "Incoming"
            incoming.mkdir(parents=True)
            (incoming / "one.jpg").write_bytes(b"tiny file")
            (vault / "note.md").write_text("![[Incoming/one.jpg]]", encoding="utf-8")

            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            results = context.Queue()
            processes = [
                context.Process(target=_import_worker, args=(str(vault), barrier, results))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(45)
                if process.is_alive():
                    process.terminate()
                    process.join()
            self.assertTrue(all(process.exitcode == 0 for process in processes))
            outcomes = [results.get(timeout=5) for _ in processes]

            self.assertEqual([outcome[0] for outcome in outcomes], ["ok", "ok"])
            self.assertEqual(sum(outcome[1] for outcome in outcomes), 1)
            self.assertEqual(sum(outcome[2] for outcome in outcomes), 0)
            objects = [path for path in (vault / "objects").iterdir() if path.is_dir() and not path.name.startswith(".")]
            self.assertEqual(len(objects), 1)
            self.assertFalse((incoming / "one.jpg").exists())
            self.assertTrue(vault_lock_path(vault).exists())


if __name__ == "__main__":
    unittest.main()
