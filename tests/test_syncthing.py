"""Opt-in integration test with two disposable, loopback-only Syncthing peers."""
import os
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from madobsidian.archive import scan_archive
from madobsidian.exclusions import append_exclusion, exclusion_path, load_exclusions, ExclusionsError
from madobsidian.importer import import_vault


SYNCTHING = os.getenv("MADOBSIDIAN_TEST_SYNCTHING") or shutil.which("syncthing")


def loopback_address():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return "tcp://127.0.0.1:" + str(sock.getsockname()[1])


@unittest.skipUnless(SYNCTHING, "requires a local Syncthing executable")
class SyncthingTests(unittest.TestCase):
    def test_sync_and_reconnect(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        homes = [root / n for n in ("peer-a", "peer-b")]
        vaults = [home / "vault" for home in homes]
        addresses = [loopback_address(), loopback_address()]
        env = {k: v for k, v in os.environ.items() if not k.startswith("ST")}
        env.update(STMONITORED="yes", STNODEFAULTFOLDER="1", STNOUPGRADE="1", STNORESTART="1")
        configs = []
        for home, vault in zip(homes, vaults):
            vault.mkdir(parents=True)
            subprocess.run([SYNCTHING, "generate", "--home", str(home), "--no-port-probing"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=30)
            configs.append(ET.parse(home / "config.xml"))
        ids = [tree.find("device").get("id") for tree in configs]
        for i, tree in enumerate(configs):
            cfg = tree.getroot()
            for folder in cfg.findall("folder"):
                cfg.remove(folder)
            folder = ET.SubElement(cfg, "folder", id="test", path=str(vaults[i]),
                                   type="sendreceive", rescanIntervalS="1")
            for identifier in ids:
                ET.SubElement(folder, "device", id=identifier)
            remote = ET.SubElement(cfg, "device", id=ids[1-i])
            ET.SubElement(remote, "address").text = addresses[1-i]
            cfg.find("gui").set("enabled", "false")
            opts = cfg.find("options")
            for key in ("globalAnnounceEnabled", "localAnnounceEnabled", "relaysEnabled",
                        "natEnabled", "crashReportingEnabled"):
                opts.find(key).text = "false"
            opts.find("listenAddress").text = addresses[i]
            opts.find("urAccepted").text = "-1"
            opts.find("limitBandwidthInLan").text = "true"
            opts.find("maxSendKbps").text = "512"
            tree.write(homes[i] / "config.xml", encoding="utf-8")

        def start(i):
            proc = subprocess.Popen([SYNCTHING, "serve", "--home", str(homes[i]), "--no-restart",
                                     "--no-browser", "--no-upgrade"], env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def stop():
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=20)
            self.addCleanup(stop)
            return stop

        def wait_until(check):
            deadline = time.monotonic() + 90
            while not check():
                self.assertLess(time.monotonic(), deadline, "test peers did not converge")
                time.sleep(0.25)

        left, right = vaults
        (left / "Incoming").mkdir()
        (left / "Incoming" / "photo.png").write_bytes(b"photo")
        (left / "note.md").write_text("[[photo.png]]")
        worker = Path(__file__).with_name("test_transaction_crashes.py")
        crashed = subprocess.run([sys.executable, str(worker), str(left), "import", "object_published"],
                                 timeout=30)
        self.assertEqual(crashed.returncode, 83)
        journal, = left.glob("objects/*/transaction.json")
        target = journal.parent.relative_to(left) / "shared" / "photo.png"
        append_exclusion(exclusion_path(left), "deferred.jpg")
        (left / "Incoming" / "deferred.jpg").write_bytes(b"waiting")
        start(0)
        stop_right = start(1)
        wait_until(lambda: (right / target).exists() and exclusion_path(right).exists()
                   and (right / "Incoming" / "deferred.jpg").exists()
                   and (right / "note.md").exists() and list(right.glob("objects/*/metadata.json"))
                   and list(right.glob("objects/*/transaction.json")))
        self.assertFalse(scan_archive(right).complete)
        self.assertEqual(len(import_vault(right).ignored), 1)
        self.assertTrue(scan_archive(right).complete)
        wait_until(lambda: not journal.exists() and target.as_posix() in (left / "note.md").read_text())
        identifier = str(uuid.uuid4())
        object_dir = left / "objects" / identifier
        (object_dir / "originals").mkdir(parents=True)
        payload = os.urandom(4 * 1024 * 1024)
        original = "originals/transfer.bin"
        (object_dir / original).write_bytes(payload)
        metadata = {"id": identifier, "original_path": original, "original_filename": "transfer.bin",
                    "mime_type": "application/octet-stream", "extension": ".bin", "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(), "imported_at": datetime.now(timezone.utc).isoformat()}
        (object_dir / "metadata.json").write_text(json.dumps(metadata))
        receiving = right / "objects" / identifier
        wait_until(lambda: any(receiving.rglob("*.tmp")))
        stop_right()
        self.assertFalse((receiving / original).exists())
        self.assertFalse(scan_archive(right).complete)
        append_exclusion(exclusion_path(left), "offline-a.jpg")
        append_exclusion(exclusion_path(right), "offline-b.jpg")
        start(1)
        wait_until(lambda: (receiving / original).is_file())
        self.assertEqual((receiving / original).read_bytes(), payload)
        wait_until(lambda: list(right.glob("madobsidian_exclusions.sync-conflict-*.ini")))
        with self.assertRaises(ExclusionsError):
            load_exclusions(exclusion_path(right))
