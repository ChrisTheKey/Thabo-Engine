"""Backup: consistent snapshot, gpg encryption, restore, download retry. Synthetic data, temp keyring."""
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lead_generation_ch_consumer import backup, server as srv

GPG = backup.gpg()
HAVE_GPG = bool(shutil.which(GPG) or Path(GPG).is_file())


def msys_path(p):
    """Git-for-Windows gpg needs /c/... style homedirs; elsewhere the path is used as-is."""
    if os.name != "nt":
        return str(p)
    return subprocess.run(["cygpath", "-u", str(p)], capture_output=True, text=True, check=True).stdout.strip()


def make_db(path, n):
    store = srv.LeadStore(path)
    for i in range(n):
        store.add(dict(first_name="B", last_name="Synthetisch", email=f"b{i}@example.invalid", phone="",
                       location="", channel="E-Mail", product="Persönliches Numerologie-Sheet",
                       request="Synthetisch"))
    store.db.close()


@unittest.skipUnless(HAVE_GPG, "gpg nicht installiert")
class BackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Short temp path: gpg-agent sockets fail on long paths.
        cls.home = Path(tempfile.mkdtemp(prefix="g"))
        cls.env = mock.patch.dict(os.environ, {"GNUPGHOME": msys_path(cls.home)})
        cls.env.start()
        subprocess.run([GPG, "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                        "--quick-generate-key", "Thabo Backup Test <backup@example.invalid>",
                        "default", "default", "never"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--kill", "all"], capture_output=True)
        cls.env.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "backups"
        self.cfg = mock.patch.dict(os.environ, {"THABO_BACKUP_GPG_RECIPIENT": "backup@example.invalid",
                                                "THABO_BACKUP_DIR": str(self.out), "THABO_BACKUP_KEEP": "2",
                                                "PA_USERNAME": "synthuser", "PA_API_TOKEN": "synthetic-token"})
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_local_backup_restore_and_rotation(self):
        db = self.tmp / "leads.sqlite3"
        make_db(db, 3)
        files = []
        for _ in range(3):
            out, n = backup.run("local", db)
            self.assertEqual(n, 3)
            files.append(out)
            time.sleep(1.1)  # distinct timestamped names
        raw = files[-1].read_bytes()
        self.assertNotIn(b"example.invalid", raw)  # encrypted, not plaintext
        self.assertNotIn(b"SQLite format", raw)
        self.assertEqual(sorted(self.out.iterdir()), files[1:])  # keep=2
        self.assertEqual(backup.restore_test(files[-1]), 3)

    def test_pull_retries_torn_download_and_hides_token(self):
        good = self.tmp / "good.sqlite3"
        make_db(good, 2)
        payloads = [b"SQLite format 3\x00 torn", good.read_bytes()]
        seen = []

        def opener(req, timeout):
            seen.append(req)
            return io.BytesIO(payloads.pop(0))

        out, n = backup.run("pull", opener=opener, wait=0)
        self.assertEqual(n, 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].get_header("Authorization"), "Token synthetic-token")
        self.assertIn("eu.pythonanywhere.com/api/v0/user/synthuser/files/path/home/synthuser/.thabo-engine/",
                      seen[0].full_url)

        def denied(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO())
        with self.assertRaises(backup.BackupError) as cm:
            backup.run("pull", opener=denied)
        self.assertNotIn("synthetic-token", str(cm.exception))

    def test_refuses_repo_backup_dir_and_missing_config(self):
        db = self.tmp / "leads.sqlite3"
        make_db(db, 1)
        with mock.patch.dict(os.environ, {"THABO_BACKUP_DIR": str(backup.REPO_ROOT / "data")}):
            with self.assertRaises(backup.BackupError):
                backup.run("local", db)
        with mock.patch.dict(os.environ, {"THABO_BACKUP_GPG_RECIPIENT": ""}), \
                mock.patch.object(backup, "ENV_FILE", self.tmp / "none"):
            with self.assertRaises(backup.BackupError):
                backup.run("local", db)

    def test_verify_rejects_non_lead_files(self):
        junk = self.tmp / "junk.sqlite3"
        junk.write_bytes(b"not a database")
        with self.assertRaises(backup.BackupError):
            backup.verify(junk)


if __name__ == "__main__":
    unittest.main()
