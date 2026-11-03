"""Encrypted lead-DB backups on this laptop (stdlib + gpg, no new dependencies).

  python -m lead_generation_ch_consumer.backup pull                 # PythonAnywhere -> laptop
  python -m lead_generation_ch_consumer.backup local <leads.sqlite3> # local DB -> laptop
  python -m lead_generation_ch_consumer.backup restore-test <file.gpg>

Config from the environment or lead_generation_ch_consumer/.env (git-ignored):
  PA_USERNAME, PA_API_TOKEN, PA_HOST (default eu.pythonanywhere.com),
  THABO_BACKUP_GPG_RECIPIENT (public key; encrypting needs no passphrase),
  THABO_BACKUP_DIR (default ~/Documents/ThaboBackups), THABO_BACKUP_KEEP (default 12).
Plaintext copies only ever live in a private temp dir and are deleted afterwards.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env")
REPO_ROOT = Path(__file__).resolve().parents[1]


class BackupError(Exception):
    pass


def setting(name, default=None):
    value = os.environ.get(name, "").strip()
    if not value and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, _, v = line.partition("=")
            if key.strip() == name:
                value = v.strip().strip('"').strip("'")
    if not value and default is None:
        raise BackupError(f"{name} nicht gesetzt (siehe README, Abschnitt Backup)")
    return value or default


def gpg():
    return os.environ.get("THABO_GPG") or shutil.which("gpg") or r"C:\Program Files\Git\usr\bin\gpg.exe"


def verify(db_path):
    """Returns the number of lead rows; raises if the file is not an intact lead DB."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("integrity_check fehlgeschlagen")
        return con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise BackupError(f"keine gültige Lead-Datenbank: {e}") from None
    finally:
        con.close()


def snapshot(src, dst):
    """Transaction-consistent copy via SQLite's online backup API."""
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def download(dst, opener=urllib.request.urlopen):
    user, token = setting("PA_USERNAME"), setting("PA_API_TOKEN")
    host = setting("PA_HOST", "eu.pythonanywhere.com")
    url = f"https://{host}/api/v0/user/{user}/files/path/home/{user}/.thabo-engine/leads.sqlite3"
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
    try:
        with opener(req, timeout=60) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        raise BackupError(f"Download fehlgeschlagen: HTTP {e.code}") from None  # never echo the token
    except urllib.error.URLError as e:
        raise BackupError(f"Netzwerkfehler: {e.reason}") from None


def encrypt_and_store(plain, backup_dir, recipient, keep):
    backup_dir = Path(backup_dir).resolve()
    if backup_dir == REPO_ROOT or REPO_ROOT in backup_dir.parents:
        raise BackupError("Backup-Ordner darf nicht im (öffentlichen) Repository liegen")
    backup_dir.mkdir(parents=True, exist_ok=True)
    out = backup_dir / f"leads-{datetime.now():%Y%m%d-%H%M%S}.sqlite3.gpg"
    r = subprocess.run([gpg(), "--batch", "--yes", "--trust-model", "always", "--recipient", recipient,
                        "--output", out.as_posix(), "--encrypt", Path(plain).as_posix()],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        raise BackupError(f"gpg-Verschlüsselung fehlgeschlagen: {r.stderr.strip()[-300:]}")
    for old in sorted(backup_dir.glob("leads-*.sqlite3.gpg"))[:-keep]:
        old.unlink()
    return out


def run(mode, source=None, opener=urllib.request.urlopen, retries=3, wait=5):
    recipient = setting("THABO_BACKUP_GPG_RECIPIENT")
    backup_dir = setting("THABO_BACKUP_DIR", str(Path.home() / "Documents" / "ThaboBackups"))
    keep = int(setting("THABO_BACKUP_KEEP", "12"))
    with tempfile.TemporaryDirectory(prefix="thabo-bk-") as tmp:
        plain = Path(tmp) / "leads.sqlite3"
        if mode == "local":
            snapshot(source, plain)
            count = verify(plain)
        else:
            # shortcut: raw file download (free plan has no scheduled tasks for a server-side
            # snapshot); a copy torn by a concurrent write fails integrity_check and is retried.
            for attempt in range(1, retries + 1):
                download(plain, opener)
                try:
                    count = verify(plain)
                    break
                except BackupError:
                    if attempt == retries:
                        raise
                    time.sleep(wait)
        out = encrypt_and_store(plain, backup_dir, recipient, keep)
    return out, count


def restore_test(encrypted):
    with tempfile.TemporaryDirectory(prefix="thabo-rt-") as tmp:
        plain = Path(tmp) / "restored.sqlite3"
        # Output not captured: gpg must be able to ask for the private-key passphrase.
        r = subprocess.run([gpg(), "--quiet", "--yes", "--output", plain.as_posix(),
                            "--decrypt", Path(encrypted).as_posix()])
        if r.returncode != 0:
            raise BackupError("Entschlüsselung fehlgeschlagen (Schlüssel/Passphrase prüfen)")
        return verify(plain)


def main(argv):
    try:
        if argv[:1] == ["restore-test"] and len(argv) == 2:
            print(f"Wiederherstellung OK: {restore_test(argv[1])} Lead-Zeilen, integrity_check ok")
        elif argv[:1] == ["local"] and len(argv) == 2:
            out, n = run("local", argv[1])
            print(f"Backup OK: {n} Lead-Zeilen -> {out}")
        elif argv == ["pull"]:
            out, n = run("pull")
            print(f"Backup OK: {n} Lead-Zeilen -> {out}")
        else:
            print(__doc__)
    except BackupError as e:
        print(f"BACKUP FEHLER: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
