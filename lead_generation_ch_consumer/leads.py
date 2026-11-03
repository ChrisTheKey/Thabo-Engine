"""Lead models, validation, scoring, dedup and CRM CSV export (stdlib only)."""
import csv
import re
from datetime import datetime

# Columns of numerology-crm/index.html (its CSV import/export header).
CRM_FIELDS = ["id", "name", "email", "phone", "location", "source", "status",
              "channel", "next", "proof", "permission", "notes"]
CRM_SOURCES = {"Kontaktformular", "Newsletter-Opt-in", "Persönliche Anfrage",
               "Empfehlung mit Kontaktfreigabe", "Event-Anmeldung mit Opt-in",
               "Andere nachweisbare Anfrage"}

SIGNAL_FIELDS = ["signal_id", "platform", "source_url", "found_at", "posted_at",
                 "need_summary", "product_fit", "score", "score_reasons",
                 "geo_evidence", "anon_ref", "review_status"]

OPT_IN_FIELDS = ["lead_id", "name", "email", "phone", "preferred_channel", "product",
                 "request", "signup_source", "consent_text", "consent_version",
                 "consent_at", "contact_permitted", "revoked", "pipeline_status",
                 "next_contact"]

# Pipeline (task spec) -> CRM status. Stages 1-2 are public signals and never reach the CRM.
PIPELINE_TO_CRM = {
    "Kontakt freiwillig angemeldet": "Neu",
    "Kontaktaufnahme erlaubt": "Interessiert",
    "Angebot gesendet": "Angebot gesendet",
    "Gekauft": "Gekauft",
    "Kein Interesse": "Abgesagt",
    "Widerspruch / gesperrt": "Abgesagt",
}
CHANNELS = {"E-Mail": "email", "Telefon": "phone"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+41|0041|\b0)\s*\d{2}[\s/.-]*\d{3}[\s/.-]*\d{2}[\s/.-]*\d{2}")


def _truthy(v):
    return str(v).strip().lower() in {"true", "1", "ja", "yes"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _cell(v):
    """Neutralise spreadsheet formulas from form input (=, +, -, @, tab, CR); plain numbers like +41... stay."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"[+-]?[\d .]+", v):
        return "'" + v
    return v


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: _cell(v) for k, v in r.items()} for r in rows)


# ---------- public intent signals ----------

def score_signal(text):
    """Heuristic 0-100 research score with reasons. Not a purchase probability,
    and never a contact permission."""
    t = text.lower()
    numerology = re.search(r"numerolog|lebenszahl|geburtszahl|schicksalszahl|seelenzahl", t)
    personal = re.search(r"\b(mein|meine|meiner|mir|mich|ich)\b|persönlich|individuell|geburtsdatum", t)
    buy = re.search(r"kost|preis|kaufen|bestell|buchen|chf|fr\.", t)
    seek = re.search(r"suche|wer (erstellt|macht)|anbieter|empfehl|wo kann ich|gibt es jemand", t)
    business = re.search(r"wiederverk|grosshandel|großhandel|agentur|b2b|für meine kunden|reseller", t)
    reasons = []
    if not numerology:
        return 0, ["kein Numerologie-Bezug"]
    reasons.append("Numerologie-Bezug")
    if business:
        return 10, reasons + ["gewerblicher Bedarf, kein Einzelkäufer"]
    if buy and personal:
        score, r = 95, "Preis-/Bestellanfrage für persönliche Auswertung"
    elif seek and personal:
        score, r = 80, "sucht aktiv Anbieter für persönliche Auswertung"
    elif personal:
        score, r = 50, "persönliche Frage ohne erkennbare Kaufabsicht"
    else:
        score, r = 20, "allgemeines Interesse"
    return score, reasons + [r]


def validate_signal(s):
    errors = []
    if not str(s.get("source_url", "")).startswith(("https://", "http://")):
        errors.append("source_url fehlt oder ungültig")
    if not s.get("signal_id"):
        errors.append("signal_id fehlt")
    # Signals must stay anonymous: no contact data for unsolicited outreach.
    for k in ("need_summary", "anon_ref", "geo_evidence"):
        v = str(s.get(k, ""))
        if EMAIL_RE.search(v) or PHONE_RE.search(v):
            errors.append(f"{k} enthält Kontaktdaten")
    return errors


def prepare_signals(signals):
    """Validate, score, dedupe (by URL). Returns (accepted, rejected_with_errors)."""
    ok, bad = [], []
    for s in dedupe(signals, lambda x: x.get("source_url", "").strip().lower().rstrip("/")):
        errs = validate_signal(s)
        if errs:
            bad.append((s, errs))
            continue
        score, reasons = score_signal(s.get("need_summary", ""))
        ok.append({**s, "score": score, "score_reasons": "; ".join(reasons)})
    return ok, bad


# ---------- opt-in leads ----------

def validate_opt_in(lead):
    errors = []
    if not lead.get("lead_id"):
        errors.append("lead_id fehlt")
    if lead.get("signup_source") not in CRM_SOURCES:
        errors.append("signup_source ist keine nachweisbare Opt-in-Quelle")
    for k in ("consent_text", "consent_version", "consent_at", "request"):
        if not str(lead.get(k, "")).strip():
            errors.append(f"{k} fehlt")
    try:
        datetime.fromisoformat(lead.get("consent_at", ""))
    except ValueError:
        errors.append("consent_at ist kein ISO-Zeitstempel")
    if lead.get("pipeline_status") not in PIPELINE_TO_CRM:
        errors.append("pipeline_status unbekannt")
    return errors


def can_contact(lead):
    """True only with valid documented consent, not revoked, not blocked,
    and contact data present for the explicitly chosen channel."""
    if validate_opt_in(lead) or _truthy(lead.get("revoked")):
        return False
    if lead.get("pipeline_status") in {"Widerspruch / gesperrt", "Kein Interesse"}:
        return False
    if not _truthy(lead.get("contact_permitted")):
        return False
    field = CHANNELS.get(lead.get("preferred_channel"))
    return bool(field and str(lead.get(field, "")).strip())


def dedupe(rows, key):
    seen, out = set(), []
    for r in rows:
        k = key(r)
        if k and k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _contact_key(lead):
    email = str(lead.get("email", "")).strip().lower()
    phone = re.sub(r"\D", "", str(lead.get("phone", "")))
    return email or phone or lead.get("lead_id")


def to_crm_rows(opt_in_leads):
    """Opt-in leads -> rows for numerology-crm CSV import. Invalid rows are rejected,
    revoked/blocked ones are exported as blocked (permission false, channel Keiner)."""
    rows, rejected = [], []
    # A revocation on any duplicate row must win over an older consent row.
    revoked_keys = {_contact_key(l) for l in opt_in_leads
                    if _truthy(l.get("revoked")) or l.get("pipeline_status") == "Widerspruch / gesperrt"}
    for lead in dedupe(opt_in_leads, _contact_key):
        if _contact_key(lead) in revoked_keys:
            lead = {**lead, "revoked": "true"}
        errs = validate_opt_in(lead)
        if errs:
            rejected.append((lead, errs))
            continue
        allowed = can_contact(lead)
        blocked = _truthy(lead.get("revoked")) or lead["pipeline_status"] == "Widerspruch / gesperrt"
        rows.append({
            "id": lead["lead_id"],
            "name": lead.get("name", "").strip() or "(ohne Namen)",
            "email": lead.get("email", ""),
            "phone": lead.get("phone", ""),
            "location": lead.get("location", ""),
            "source": lead["signup_source"],
            "status": "Abgesagt" if blocked else PIPELINE_TO_CRM[lead["pipeline_status"]],
            "channel": lead["preferred_channel"] if allowed else "Keiner",
            "next": lead.get("next_contact", "") if allowed else "",
            "proof": (f"{lead['signup_source']} {lead['consent_at']}, Einwilligung "
                      f"v{lead['consent_version']}: {lead['request']}"),
            "permission": "true" if allowed else "false",
            "notes": "GESPERRT: Einwilligung widerrufen / Widerspruch" if blocked else lead.get("notes", ""),
        })
    return rows, rejected
