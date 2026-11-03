"""One opt-in sync cycle: lead DB -> Lead-Sammelordner (qualified leads) + CRM import CSV.

  python -m lead_generation_ch_consumer.sync_service run      # background loop (leadsync.ps1 START)
  python -m lead_generation_ch_consumer.sync_service cycle    # one cycle now
  python -m lead_generation_ch_consumer.sync_service report

Source: the PythonAnywhere lead DB when PA_USERNAME/PA_API_TOKEN are configured (same download as
backup.py), else the local DB in THABO_DATA_DIR. No web scraping and no search.ch: the only leads that
are both lawful and contactable are people who entered their own data and consent in the form.
Both CSVs are rewritten atomically from the current DB every cycle, so they never hold half rows,
duplicates or leads that were revoked or deleted in the meantime.
"""
import csv
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import backup
from .leads import CRM_FIELDS, write_csv
from .server import LeadStore, data_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER = Path(os.environ.get("THABO_LEAD_FOLDER") or REPO_ROOT / "Lead-Sammelordner")
LEADS_CSV = "numerologie_leads.csv"
CRM_CSV = "crm_import.csv"
LOG = "zyklen.log"
FIELDS = ["Lead-ID", "Name", "Telefonnummer", "E-Mail-Adresse", "Interessensnachweis", "Quellen-URL",
          "Prüfdatum", "Kontaktberechtigung", "Kontaktstatus"]


def qualified(row):
    """Importable: name, phone or email, documented interest, granted permission, not blocked/deleted."""
    return bool(row.get("name", "").strip() and row["name"] != "(ohne Namen)"
                and (row.get("phone", "").strip() or row.get("email", "").strip())
                and row.get("proof", "").strip()
                and row.get("permission") == "true"
                and row.get("status") != "Abgesagt")


def _replace(path, write):
    """Write to a temp file in the same folder, then swap it in: readers never see a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def publish(rows, source_url, folder=FOLDER, now=None):
    """Writes both CSVs from the CRM rows; returns the qualified lead rows."""
    folder.mkdir(parents=True, exist_ok=True)
    checked = (now or datetime.now()).isoformat(timespec="seconds")
    leads = [{"Lead-ID": r["id"], "Name": r["name"], "Telefonnummer": r.get("phone", ""),
              "E-Mail-Adresse": r.get("email", ""), "Interessensnachweis": r["proof"],
              "Quellen-URL": source_url, "Prüfdatum": checked, "Kontaktberechtigung": r["permission"],
              "Kontaktstatus": r["status"]} for r in rows if qualified(r)]
    # The CRM file keeps blocked/deleted rows too: its import needs them to block or remove old entries.
    _replace(folder / CRM_CSV, lambda p: write_csv(p, CRM_FIELDS, rows))
    _replace(folder / LEADS_CSV, lambda p: write_csv(p, FIELDS, leads))
    return leads


def _crm_rows(db):
    store = LeadStore(db)
    try:
        return store.crm_rows()[0]
    finally:
        store.db.close()


def cycle(folder=FOLDER, opener=None):
    """One sync. Never raises: every outcome ends up as one log line without personal data."""
    started = datetime.now()
    try:
        user = backup.setting("PA_USERNAME", "")
        with tempfile.TemporaryDirectory(prefix="thabo-sync-") as tmp:
            if user:
                db, source = Path(tmp) / "leads.sqlite3", f"https://{user}.eu.pythonanywhere.com/"
                backup.download(db, *([opener] if opener else []))
                backup.verify(db)
            else:
                db, source = data_dir() / "leads.sqlite3", "lokales Kontaktformular (THABO_DATA_DIR)"
                if not db.is_file():
                    if not (folder / LEADS_CSV).is_file():  # header-only; never wipe leads on a config gap
                        folder.mkdir(parents=True, exist_ok=True)
                        _replace(folder / LEADS_CSV, lambda p: write_csv(p, FIELDS, []))
                    return _log(folder, started, "keine Quelle: weder PythonAnywhere konfiguriert noch lokale Lead-DB", 0, 0)
            rows = _crm_rows(db)
        leads = publish(rows, source, folder, started)
        return _log(folder, started, f"OK Quelle={source}", len(rows), len(leads))
    except Exception as e:  # one failed cycle must not stop the next one; messages carry no tokens
        return _log(folder, started, f"FEHLER {type(e).__name__}: {e}", 0, 0)


def _log(folder, started, msg, rows, leads):
    folder.mkdir(parents=True, exist_ok=True)
    line = f"{started.isoformat(timespec='seconds')} | {msg} | CRM-Zeilen={rows} | qualifiziert={leads}"
    with open(folder / LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def report(folder=FOLDER):
    log, csv_path = folder / LOG, folder / LEADS_CSV
    last = log.read_text(encoding="utf-8").splitlines()[-1] if log.is_file() else "noch kein Zyklus"
    count = 0
    if csv_path.is_file():
        with open(csv_path, newline="", encoding="utf-8") as f:
            count = sum(1 for _ in csv.DictReader(f))
    return f"Letzter Zyklus: {last}\nGespeicherte Leads: {count}\nCSV: {csv_path}\nCRM-Import: {folder / CRM_CSV}"


def run(interval=3600):
    """The background process leadsync.ps1 starts: one cycle per hour until STOP kills it."""
    while True:
        cycle()
        time.sleep(interval)


if __name__ == "__main__":
    if sys.argv[1:] == ["run"]:
        run()
    elif sys.argv[1:] == ["cycle"]:
        print(cycle())
    elif sys.argv[1:] == ["report"]:
        print(report())
    else:
        print(__doc__)
