"""Hourly cycle: opt-in leads -> Lead-Sammelordner + CRM import CSV, plus anonymous feed research.

  python -m lead_generation_ch_consumer.sync_service run      # background loop (leadsync.ps1 START)
  python -m lead_generation_ch_consumer.sync_service cycle    # one cycle now
  python -m lead_generation_ch_consumer.sync_service report

Leads: the PythonAnywhere lead DB when PA_USERNAME/PA_API_TOKEN are configured (same download as
backup.py), else the local DB in THABO_DATA_DIR. Only people who entered their own data and consent
there are contactable. Both CSVs are rewritten atomically from the DB every cycle, so they never hold
half rows, duplicates or leads that were revoked or deleted in the meantime.

Research: RSS/Atom feeds listed in Lead-Sammelordner/quellen.txt. A feed is read only if robots.txt
allows it; the post texts inside the feed are scored and anonymised (research.extract_signal). Item
pages are never fetched: fewer requests, works for JS-rendered sites, no SSRF via feed links. Public posts
are pseudonymous, so research yields anonymous signals only, never leads; search.ch is not used, as
looking up names from posts would join directory data with interest profiles.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import backup, research
from .leads import CRM_FIELDS, SIGNAL_FIELDS, prepare_signals, read_csv, signal_category, write_csv
from .server import LeadStore, data_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER = Path(os.environ.get("THABO_LEAD_FOLDER") or REPO_ROOT / "Lead-Sammelordner")
LEADS_CSV = "numerologie_leads.csv"
CRM_CSV = "crm_import.csv"
SIGNALS_CSV = "recherche_signale.csv"
SOURCES = "quellen.txt"
LAST_RESEARCH = "recherche_letzter_zyklus.txt"
LOG = "zyklen.log"
FIELDS = ["Lead-ID", "Name", "Telefonnummer", "E-Mail-Adresse", "Interessensnachweis", "Quellen-URL",
          "Prüfdatum", "Kontaktberechtigung", "Darf angerufen werden", "Begründung Anrufstatus", "Kontaktstatus"]
# Checked 2026-11-03: the forum feeds refuse automated access, the fediverse hashtag feeds allow it (see README).
DEFAULT_SOURCES = """# Ein RSS/Atom-Feed pro Zeile. Nur Quellen eintragen, deren Nutzungsbedingungen geprüft sind.
https://www.gutefrage.net/tag/numerologie/rss
https://www.reddit.com/r/Numerologie/new/.rss
https://www.esoterikforum.at/forums/numerologie.98/index.rss
https://www.astrologie-forum.de/rss
https://www.paranormal.de/forum/external.php?type=RSS2
# Fediverse-Hashtag-Feeds (robots.txt erlaubt, geprüft 2026-11-03)
https://swiss.social/tags/numerologie.rss
https://mastodon.social/tags/numerologie.rss
https://mastodon.social/tags/lebenszahl.rss
https://chaos.social/tags/numerologie.rss
# Bluesky-Volltextsuche (öffentliche API ohne Login, nur lesend; Developer Guidelines geprüft 2026-11-03)
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=numerologie&lang=de&limit=20&sort=latest
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=lebenszahl&lang=de&limit=20&sort=latest
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=numerologie%20beratung&lang=de&limit=20&sort=latest
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=lebensweg%20analyse&lang=de&limit=20&sort=latest
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=numerologie%20kosten&lang=de&limit=20&sort=latest
"""
FEED_ITEMS = 20  # per feed and cycle


def qualified(row):
    """Importable: name, phone or email, documented interest, granted permission, not blocked/deleted."""
    return bool(row.get("name", "").strip() and row["name"] != "(ohne Namen)"
                and (row.get("phone", "").strip() or row.get("email", "").strip())
                and row.get("proof", "").strip()
                and row.get("permission") == "true"
                and row.get("status") != "Abgesagt")


def call_status(row):
    """(JA|NEIN|UNGEKLÄRT, reason). Consent covers only the channel the person chose; unclear never becomes JA."""
    if row.get("permission") != "true" or row.get("status") == "Abgesagt":
        return "NEIN", "keine Kontaktberechtigung oder Widerruf/Sperre"
    if not row.get("phone", "").strip():
        return "NEIN", "keine Telefonnummer angegeben"
    if row.get("channel") == "Telefon":
        return "JA", "eigene Anfrage mit Einwilligung zur Kontaktaufnahme per Telefon"
    if row.get("channel") in ("E-Mail", "Keiner"):
        return "NEIN", f"Einwilligung gilt nur für den gewählten Kanal {row.get('channel')}"
    return "UNGEKLÄRT", "Kontaktkanal unbekannt"


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
    leads, crm = [], []
    for r in rows:
        if qualified(r):
            status, reason = call_status(r)
            leads.append({"Lead-ID": r["id"], "Name": r["name"], "Telefonnummer": r.get("phone", ""),
                          "E-Mail-Adresse": r.get("email", ""), "Interessensnachweis": r["proof"],
                          "Quellen-URL": source_url, "Prüfdatum": checked, "Kontaktberechtigung": r["permission"],
                          "Darf angerufen werden": status, "Begründung Anrufstatus": reason,
                          "Kontaktstatus": r["status"]})
            # The CRM schema has no call-status column; its notes field carries it.
            r = {**r, "notes": f"Anruf: {status} ({reason}). {r.get('notes', '')}".strip()}
        crm.append(r)
    # The CRM file keeps blocked/deleted rows too: its import needs them to block or remove old entries.
    _replace(folder / CRM_CSV, lambda p: write_csv(p, CRM_FIELDS, crm))
    _replace(folder / LEADS_CSV, lambda p: write_csv(p, FIELDS, leads))
    return leads


def _crm_rows(db):
    store = LeadStore(db)
    try:
        return store.crm_rows()[0]
    finally:
        store.db.close()


def _date(v):
    """YYYY-MM-DD from an RSS (RFC 822), Atom or Bluesky (ISO) timestamp; '' if unparseable."""
    v = (v or "").strip()
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00")) if v[:4].isdigit() else parsedate_to_datetime(v)
        return d.date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def feed_items(xml_text):
    """(post URL, post HTML, post date) from an RSS or Atom feed."""
    # Untrusted input: refuse DTDs/entities outright (no entity expansion), no new dependency needed.
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise ValueError("Feed mit DTD/Entity abgelehnt")
    root = ET.fromstring(xml_text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = [(i.findtext("link", ""), i.findtext("description", ""), _date(i.findtext("pubDate")))
             for i in root.iter("item")]
    for e in root.iter(atom + "entry"):
        href = next((l.get("href", "") for l in e.findall(atom + "link") if l.get("rel", "alternate") == "alternate"), "")
        items.append((href, e.findtext(atom + "content") or e.findtext(atom + "summary") or "",
                      _date(e.findtext(atom + "published") or e.findtext(atom + "updated"))))
    return [(u.strip(), d, t) for u, d, t in items
            if u.strip().startswith(("https://", "http://")) and _host(u.strip())][:FEED_ITEMS]


def bsky_items(json_text):
    """(post URL, text, date) from a public app.bsky.feed.searchPosts response (read-only, no login)."""
    out = []
    for p in json.loads(json_text).get("posts", []):
        parts = str(p.get("uri", "")).split("/")  # at://<did>/app.bsky.feed.post/<rkey>
        rec = p.get("record") or {}
        if len(parts) == 5 and parts[3] == "app.bsky.feed.post" and isinstance(rec.get("text"), str):
            out.append((f"https://bsky.app/profile/{parts[2]}/post/{parts[4]}", rec["text"], _date(rec.get("createdAt"))))
    return out[:FEED_ITEMS]


def _host(url):
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:  # malformed URL from an untrusted feed
        return None


def research_cycle(folder=FOLDER, fetch=None):
    """Returns (sources, new_signals, per-source status lines). Skipped sources are only logged."""
    fetch = fetch or research._fetch
    src_file = folder / SOURCES
    if not src_file.is_file():
        src_file.write_text(DEFAULT_SOURCES, encoding="utf-8")
    sources = [l.strip() for l in src_file.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.lstrip().startswith("#")]
    status, found = [], []
    for src in sources:
        if not research.allowed_by_robots(src, fetch):
            status.append(f"übersprungen: robots.txt verbietet oder unklar | {src}")
            continue
        try:  # one bad feed or item skips only this source, never the whole research run
            raw = fetch(src)
            items = bsky_items(raw) if _host(src) == "api.bsky.app" else feed_items(raw)
            sigs = [{**s, "posted_at": t} for s, t in ((research.extract_signal(u, d), t) for u, d, t in items) if s]
        except urllib.error.HTTPError as e:
            status.append(f"übersprungen: Server verweigert (HTTP {e.code}) | {src}")
            continue
        except Exception as e:
            status.append(f"übersprungen: {type(e).__name__} | {src}")
            continue
        # Counts only: post URLs can contain pseudonyms, so they are not written to the status file.
        cats = Counter(s["product_fit"] for s in sigs)
        status.append(f"gelesen: {len(items)} Beiträge, {len(sigs)} mit Numerologie-Bezug "
                      f"({', '.join(f'{k} {v}' for k, v in sorted(cats.items())) or '-'}) | {src}")
        found += sigs
    out = folder / SIGNALS_CSV
    existing = read_csv(out) if out.is_file() else []
    known = {s["source_url"] for s in existing}
    new, _ = prepare_signals([s for s in found if s["source_url"] not in known])
    merged, _ = prepare_signals(existing + new)  # dedupe by URL, re-scores with the current rules
    merged = [{**s, "product_fit": signal_category(s["score"])} for s in merged]  # older rows get today's categories
    _replace(out, lambda p: write_csv(p, SIGNAL_FIELDS, merged))
    return len(sources), new, status


def cycle(folder=FOLDER, opener=None, fetch=None, pull_run=None):
    """One sync + research run. Never raises: each outcome ends up as one log line without personal data."""
    started = datetime.now()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        lead_msg, rows, leads = _lead_part(folder, started, opener)
    except Exception as e:  # one failed part must not stop the next cycle; messages carry no tokens
        lead_msg, rows, leads = f"Leads FEHLER {type(e).__name__}: {e}", 0, []
    try:
        n_src, new, status = research_cycle(folder, fetch)
        skipped = sum(s.startswith("übersprungen") for s in status[:n_src])
        buyers = sum(s["product_fit"] == "Kaufinteresse" for s in new)
        res_msg = (f"Recherche: {n_src} Quellen, {skipped} übersprungen, {len(new)} neue anonyme Signale, "
                   f"davon Kaufinteresse={buyers}")
        _replace(folder / LAST_RESEARCH, lambda p: Path(p).write_text(
            f"{started.isoformat(timespec='seconds')}\n" + "\n".join(status) + "\n", encoding="utf-8"))
    except Exception as e:
        res_msg = f"Recherche FEHLER {type(e).__name__}: {e}"
    b2b_msg = b2b_pull(folder, **({"run": pull_run} if pull_run else {}))
    call = {k: sum(l["Darf angerufen werden"] == k for l in leads) for k in ("JA", "NEIN", "UNGEKLÄRT")}
    return _log(folder, started, f"{lead_msg} | CRM-Zeilen={rows} | qualifiziert={len(leads)} | "
                                 f"Anruf JA/NEIN/UNGEKLÄRT={call['JA']}/{call['NEIN']}/{call['UNGEKLÄRT']} | {res_msg}"
                                 f" | {b2b_msg}")


# Hetzner is the primary B2B source; the laptop only pulls copies (B2B research never runs on Windows).
B2B_REMOTE = os.environ.get("THABO_B2B_REMOTE", "fiverr:thabo-b2b/data")
_WIN_SCP = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "scp.exe"
SCP = os.environ.get("THABO_SCP") or (str(_WIN_SCP) if _WIN_SCP.is_file() else "scp")  # Windows agent holds the key


def b2b_pull(folder=FOLDER, run=subprocess.run):
    """Download the server's B2B CSVs over scp (BatchMode: never prompts). A file is swapped in atomically only
    if it is complete: expected header and no fewer rows than the local copy. Never raises."""
    from . import b2b  # late: b2b imports this module
    try:
        counts = []
        for name, fields in ((b2b.B2B_CSV, b2b.FIELDS), (b2b.B2B_CRM, CRM_FIELDS)):
            part, final = folder / f".{name}.part", folder / name
            try:
                r = run([SCP, "-q", "-B", "-o", "ConnectTimeout=20", f"{B2B_REMOTE}/{name}", str(part)],
                        capture_output=True, text=True, timeout=120,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0:
                    return f"B2B-Sync FEHLER scp {r.returncode}: {(r.stderr or '').strip()[-200:]}"
                with open(part, newline="", encoding="utf-8") as f:
                    header = next(csv.reader(f), [])
                new_rows, old_rows = read_csv(part), (read_csv(final) if final.is_file() else [])
                if header != fields:
                    return f"B2B-Sync FEHLER {name}: unerwarteter Spaltenkopf, lokale Datei unverändert"
                if len(new_rows) < len(old_rows):  # the server only ever adds rows: fewer means a broken copy
                    return f"B2B-Sync FEHLER {name}: {len(new_rows)} < {len(old_rows)} Zeilen, lokale Datei unverändert"
                os.replace(part, final)
                counts.append(len(new_rows))
            finally:
                if part.exists():
                    part.unlink()
        return f"B2B-Sync OK: {counts[0]} B2B-Leads ({datetime.now().isoformat(timespec='seconds')})"
    except Exception as e:
        return f"B2B-Sync FEHLER {type(e).__name__}: {e}"


def _lead_part(folder, started, opener):
    user = backup.setting("PA_USERNAME", "")
    with tempfile.TemporaryDirectory(prefix="thabo-sync-") as tmp:
        if user:
            db, source = Path(tmp) / "leads.sqlite3", f"https://{user}.eu.pythonanywhere.com/"
            backup.download(db, *([opener] if opener else []))
            backup.verify(db)
        else:
            db, source = data_dir() / "leads.sqlite3", "lokales Kontaktformular (THABO_DATA_DIR)"
            if not db.is_file():
                # (Re)write an empty file with the current header; never wipe existing leads on a config gap.
                if not (folder / LEADS_CSV).is_file() or not read_csv(folder / LEADS_CSV):
                    _replace(folder / LEADS_CSV, lambda p: write_csv(p, FIELDS, []))
                return "Leads: keine Quelle (weder PythonAnywhere noch lokale Lead-DB)", 0, []
        rows = _crm_rows(db)
    return f"Leads OK Quelle={source}", len(rows), publish(rows, source, folder, started)


def _log(folder, started, msg):
    line = f"{started.isoformat(timespec='seconds')} | {msg}"
    with open(folder / LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def report(folder=FOLDER):
    log, csv_path, sig = folder / LOG, folder / LEADS_CSV, folder / SIGNALS_CSV
    last = log.read_text(encoding="utf-8").splitlines()[-1] if log.is_file() else "noch kein Zyklus"
    count = 0
    if csv_path.is_file():
        with open(csv_path, newline="", encoding="utf-8") as f:
            count = sum(1 for _ in csv.DictReader(f))
    signals = len(read_csv(sig)) if sig.is_file() else 0
    detail = (folder / LAST_RESEARCH).read_text(encoding="utf-8").strip() if (folder / LAST_RESEARCH).is_file() else ""
    return (f"Letzter Zyklus: {last}\nGespeicherte Leads: {count}\nCSV: {csv_path}\nCRM-Import: {folder / CRM_CSV}"
            f"\nB2B-Leads von Hetzner: {_b2b_count(folder)} ({folder / 'numerology_b2b_leads.csv'})"
            f"\nAnonyme Recherche-Signale: {signals} ({sig})\nQuellen: {folder / SOURCES}\n\nLetzte Recherche:\n{detail}")


def _b2b_count(folder):
    path = folder / "numerology_b2b_leads.csv"
    return len(read_csv(path)) if path.is_file() else 0


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
