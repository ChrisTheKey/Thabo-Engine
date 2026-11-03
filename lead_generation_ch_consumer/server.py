"""Opt-in lead backend for numerology-landing (stdlib only).

Run:    python -m lead_generation_ch_consumer.server serve [--port 8080]
Export: python -m lead_generation_ch_consumer.server export <crm_import.csv>

Data lives in THABO_DATA_DIR (default ~/.thabo-engine), never inside the repo.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .leads import CRM_FIELDS, score_signal, to_crm_rows, write_csv

REPO_ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = REPO_ROOT / "numerology-landing"

# Bump the version whenever the consent text on the landing page changes.
CONSENT_VERSION = "2026-11-03.1"
CONSENT_TEXT = ("Ich willige ein, dass Thabo Numerology mich über den gewählten Kontaktkanal "
                "zu meiner Anfrage für ein persönliches Numerologie-Sheet kontaktiert. "
                "Ich kann diese Einwilligung jederzeit widerrufen.")
PRODUCTS = {"Persönliches Numerologie-Sheet", "Ausführliche Numerologie-Analyse", "Noch unsicher – bitte beraten"}
CHANNELS = {"E-Mail", "Telefon"}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
MIN_FILL_SECONDS = 3
RATE_LIMIT = (5, 3600)  # max submissions per IP per window (seconds)
MAX_BODY = 10_000

SCHEMA = """CREATE TABLE IF NOT EXISTS leads(
  lead_id TEXT PRIMARY KEY, created_at TEXT, first_name TEXT, last_name TEXT,
  email TEXT UNIQUE, phone TEXT, location TEXT, channel TEXT, product TEXT, request TEXT,
  consent_text TEXT, consent_version TEXT, consent_at TEXT, revoke_hash TEXT,
  revoked_at TEXT, deleted_at TEXT, pipeline_status TEXT, score INTEGER, score_reasons TEXT)"""


class ValidationError(ValueError):
    pass


def data_dir():
    d = Path(os.environ.get("THABO_DATA_DIR") or Path.home() / ".thabo-engine").resolve()
    if d == REPO_ROOT or REPO_ROOT in d.parents:
        raise RuntimeError("THABO_DATA_DIR darf nicht im (öffentlichen) Repository liegen")
    d.mkdir(parents=True, exist_ok=True)
    return d


def normalize_phone(raw):
    """Swiss numbers only -> +41XXXXXXXXX. Empty stays empty."""
    s = re.sub(r"[\s/().-]", "", raw or "")
    if not s:
        return ""
    if s.startswith("0041"):
        s = "+41" + s[4:]
    elif s.startswith("0"):
        s = "+41" + s[1:]
    if not re.fullmatch(r"\+41[1-9]\d{8}", s):
        raise ValidationError("Telefonnummer: bitte Schweizer Nummer, z. B. +41 79 000 00 00")
    return s


def _clean(data, key, maxlen, required=False):
    v = str(data.get(key) or "").replace("\r\n", "\n").strip()
    if required and not v:
        raise ValidationError(f"{key} fehlt")
    if len(v) > maxlen or any(ord(c) < 32 for c in v.replace("\n", "")):
        raise ValidationError(f"{key} ungültig")
    return v


def validate_submission(data):
    """Returns a cleaned dict or raises ValidationError. Spam checks are separate."""
    first = _clean(data, "first_name", 80, True)
    last = _clean(data, "last_name", 80, True)
    email = _clean(data, "email", 254, True).lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("E-Mail-Adresse ungültig")
    phone = normalize_phone(_clean(data, "phone", 30))
    location = _clean(data, "location", 80)
    channel = _clean(data, "channel", 20, True)
    if channel not in CHANNELS:
        raise ValidationError("Kontaktkanal ungültig")
    if channel == "Telefon" and not phone:
        raise ValidationError("Für Kontakt per Telefon bitte Telefonnummer angeben")
    product = _clean(data, "product", 80, True)
    if product not in PRODUCTS:
        raise ValidationError("Produkt ungültig")
    request = _clean(data, "request", 1000, True)
    if data.get("consent") is not True:  # must be an explicit, active tick
        raise ValidationError("Einwilligung zur Kontaktaufnahme fehlt")
    if data.get("consent_version") != CONSENT_VERSION:
        raise ValidationError("Einwilligungstext veraltet – Seite bitte neu laden")
    return dict(first_name=first, last_name=last, email=email, phone=phone, location=location,
                channel=channel, product=product, request=request)


class SpamGuard:
    def __init__(self, limit=RATE_LIMIT, clock=time.time):
        self.max, self.window = limit
        self.clock = clock
        self.hits = {}
        self.lock = threading.Lock()

    def check(self, ip, data):
        """Returns None if ok, else a reason. Honeypot hits are silently 'accepted'."""
        if data.get("website"):
            return "honeypot"
        try:
            started = float(data.get("started_at", 0)) / 1000
        except (TypeError, ValueError):
            started = 0
        if started <= 0 or self.clock() - started < MIN_FILL_SECONDS:
            return "too_fast"
        with self.lock:
            now = self.clock()
            recent = [t for t in self.hits.get(ip, []) if now - t < self.window]
            if len(recent) >= self.max:
                return "rate_limited"
            self.hits[ip] = recent + [now]
        return None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class LeadStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.db:
            self.db.execute(SCHEMA)

    def add(self, lead):
        """Insert a validated lead. Returns (status, revoke_token|None).
        Duplicate active email -> 'duplicate' (no change). Re-signup after revocation
        records the new consent."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # The form itself is a personal numerology enquiry; the request text decides buy vs. seek.
        score, reasons = score_signal(f"persönliche Numerologie-Anfrage: {lead['product']}. {lead['request']}")
        token = secrets.token_urlsafe(24)
        fields = dict(lead, consent_text=CONSENT_TEXT, consent_version=CONSENT_VERSION,
                      consent_at=now, revoke_hash=_hash(token), revoked_at=None,
                      pipeline_status="Kontakt freiwillig angemeldet",
                      score=score, score_reasons="; ".join(reasons))
        with self.lock, self.db:
            row = self.db.execute("SELECT lead_id, revoked_at FROM leads WHERE email=?",
                                  (lead["email"],)).fetchone()
            if row and not row["revoked_at"]:
                return "duplicate", None
            if row:
                sets = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE leads SET {sets} WHERE lead_id=?",
                                (*fields.values(), row["lead_id"]))
                return "renewed", token
            fields.update(lead_id="TN-" + secrets.token_hex(6).upper(), created_at=now)
            self.db.execute(f"INSERT INTO leads({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
                            tuple(fields.values()))
            return "created", token

    def revoke(self, token, delete=False):
        """Revoke consent; with delete=True also erase all personal data (tombstone stays)."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.lock, self.db:
            row = self.db.execute("SELECT lead_id FROM leads WHERE revoke_hash=?",
                                  (_hash(token or ""),)).fetchone()
            if not row:
                return False
            if delete:
                self.db.execute(
                    "UPDATE leads SET first_name='', last_name='', email=NULL, phone='', location='',"
                    " request='', revoke_hash=NULL, revoked_at=COALESCE(revoked_at, ?), deleted_at=?,"
                    " pipeline_status='Widerspruch / gesperrt' WHERE lead_id=?", (now, now, row["lead_id"]))
            else:
                self.db.execute("UPDATE leads SET revoked_at=?, pipeline_status='Widerspruch / gesperrt'"
                                " WHERE lead_id=?", (now, row["lead_id"]))
            return True

    def crm_rows(self):
        rows = self.db.execute("SELECT * FROM leads ORDER BY created_at").fetchall()
        live = [r for r in rows if not r["deleted_at"]]
        opt_in = [{
            "lead_id": r["lead_id"], "name": f"{r['first_name']} {r['last_name']}".strip(),
            "email": r["email"] or "", "phone": r["phone"], "preferred_channel": r["channel"],
            "product": r["product"], "request": f"{r['product']}: {r['request']}",
            "signup_source": "Kontaktformular", "consent_text": r["consent_text"],
            "consent_version": r["consent_version"], "consent_at": r["consent_at"],
            "contact_permitted": "true", "revoked": "true" if r["revoked_at"] else "false",
            "pipeline_status": r["pipeline_status"], "next_contact": "",
            "location": r["location"],
            "notes": f"Interessensscore {r['score']} ({r['score_reasons']})",
        } for r in live]
        out, rejected = to_crm_rows(opt_in)
        # Tombstones carry no personal data; the CRM import removes the matching id.
        out += [{"id": r["lead_id"], "permission": "false", "status": "Abgesagt", "channel": "Keiner",
                 "notes": "GELÖSCHT: Daten auf Wunsch gelöscht"} for r in rows if r["deleted_at"]]
        return out, rejected


def make_handler(store, guard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ThaboLanding"
        sys_version = ""

        def log_message(self, fmt, *args):  # no IPs, bodies or query strings in logs
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}\n")

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            files = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js"}
            name = files.get(self.path.split("?")[0])
            if not name:
                return self._send(404, {"error": "not found"})
            ctype = "text/html; charset=utf-8" if name.endswith(".html") else "text/javascript; charset=utf-8"
            self._send(200, (LANDING_DIR / name).read_bytes(), ctype)

        def _json(self):
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValidationError("Content-Type muss application/json sein")
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BODY:
                raise ValidationError("Anfrage zu gross oder leer")
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise ValidationError("Ungültige Anfrage")
            return data

        def do_POST(self):
            try:
                data = self._json()
                if self.path == "/api/lead":
                    reason = guard.check(self.client_address[0], data)
                    if reason == "honeypot":
                        return self._send(200, {"ok": True})  # don't tell bots
                    if reason:
                        return self._send(429, {"error": "Bitte kurz warten und erneut versuchen."})
                    status, token = store.add(validate_submission(data))
                    # Same answer for new and duplicate: don't reveal whether an email is stored.
                    return self._send(200, {"ok": True, "revoke_token": token})
                if self.path == "/api/revoke":
                    ok = store.revoke(str(data.get("token") or ""), delete=data.get("delete") is True)
                    return self._send(200 if ok else 404,
                                      {"ok": ok} if ok else {"error": "Widerrufscode unbekannt"})
                return self._send(404, {"error": "not found"})
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                msg = str(e) if isinstance(e, ValidationError) else "Ungültige Anfrage"
                return self._send(400, {"error": msg})
            except Exception:
                return self._send(500, {"error": "Serverfehler"})

    return Handler


def main(argv):
    store = LeadStore(data_dir() / "leads.sqlite3")
    if argv[:1] == ["export"] and len(argv) == 2:
        rows, rejected = store.crm_rows()
        write_csv(argv[1], CRM_FIELDS, rows)
        print(f"{len(rows)} Zeilen exportiert, {len(rejected)} abgelehnt -> {argv[1]}")
        return
    if argv[:1] == ["serve"]:
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8080
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, SpamGuard()))
        print(f"Landingpage: http://127.0.0.1:{port}/  (Daten: {data_dir()})")
        srv.serve_forever()
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
