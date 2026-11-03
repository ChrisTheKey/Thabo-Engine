"""Swiss B2B partner search: esoteric shops, astrology/tarot consultations, spiritual coaches.

  python -m lead_generation_ch_consumer.b2b run      # background loop (systemd on the server)
  python -m lead_generation_ch_consumer.b2b cycle    # one cycle now
  python -m lead_generation_ch_consumer.b2b report

Source: OpenStreetMap via the Swiss OSM association's Overpass instance (overpass.osm.ch, Swiss data only;
robots.txt allows it, the SOSM terms set no ban on automated use but reserve limits, so it is used lightly:
one ~20 s query per day, even though the loop wakes hourly). Data © OpenStreetMap contributors, ODbL;
internal use only (no publication of the lists). search.ch is not used (its terms forbid bulk queries).
Every hit is a *potential* sales partner. Public business contact data is no advertising permission, so the
contact status is never set to JA automatically.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import research
from .leads import CRM_FIELDS, read_csv, write_csv
from .sync_service import _replace

OVERPASS = "https://overpass.osm.ch/api/interpreter"
SOURCE_MIN_AGE = timedelta(hours=24)  # OSM changes slowly; spare the volunteer-run server
REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER = Path(os.environ.get("THABO_B2B_FOLDER") or REPO_ROOT / "Lead-Sammelordner")
B2B_CSV = "numerology_b2b_leads.csv"
B2B_CRM = "numerology_b2b_crm_import.csv"
LOG = "b2b_zyklen.log"
STATE = "b2b_letzte_abfrage.txt"
FIELDS = ["Lead-ID", "Firmenname", "Geschäftskategorie", "Strasse", "PLZ", "Ort", "Kanton", "Website", "Telefon",
          "E-Mail", "Ansprechpartner", "Quellen-URL", "Erfassungsdatum", "Partnerschaftseignung", "Partnerstatus",
          "Kontaktstatus", "Begründung Kontaktstatus"]

KINDS = ["shop", "office", "healthcare", "amenity", "craft"]
_W, _E = "(^|[^[:alpha:]])", "([^[:alpha:]]|$)"  # word boundaries: 'Freikirche' is no 'reiki', 'Medialine' no 'medial'
NAME_RE = (f"esoter|ésotér|astrolog|numerolog|schaman|chamani|sciaman|chakra|hellsicht|hellseh|kartenleg|wahrsag|"
           f"cartomant|geistheil|lichtarbeit|spirituel|spiritual|{_W}tarot|{_W}reiki|{_W}voyance|{_W}voyant|"
           f"{_W}médium{_E}|{_W}medial(e|ität){_E}")
QUERY = ('[out:json][timeout:90];(nwr["shop"~"^(esoteric|spiritual|psychic|new_age|occult|crystal)$"];'
         + "".join(f'nwr["{k}"]["name"~"{NAME_RE}",i];' for k in KINDS) + ")->.found;.found out center tags;"
         'area["ISO3166-2"~"^CH-"][admin_level=4]->.cantons;foreach.cantons->.c(nwr.found(area.c);out ids;.c out tags;);')

DENY = {"brothel", "place_of_worship", "insurance", "social_facility", "bank", "restaurant", "bar", "pub", "cafe",
        "nightclub", "hotel", "school", "pharmacy", "electronics"}
PRIO = {"CH-BE": 1, "CH-ZH": 2, "CH-BS": 3, "CH-BL": 3, "CH-LU": 4, "CH-GE": 6, "CH-VD": 6, "CH-NE": 6, "CH-JU": 6,
        "CH-FR": 6, "CH-VS": 6, "CH-TI": 7}  # all other cantons: 5 (rest of German-speaking Switzerland)
FIT = {"Esoterik-/Spiritualitätsgeschäft": "hoch: Laden kann Reports anbieten oder auslegen",
       "Astrologie-/Tarot-Beratung": "hoch: passendes Zusatzangebot für die eigene Kundschaft",
       "Spiritueller Coach / Praxis": "mittel: Ergänzung zur Beratung"}
REASON = ("Öffentliche Geschäftsangaben (OpenStreetMap), keine Einwilligung. Vor einem Anruf den Sterneintrag per "
          "search.ch-Einzelabfrage prüfen; nur individuelle, sachbezogene Anfrage, keine Massenwerbung "
          "(UWG Art. 3 Abs. 1 lit. o/u).")


def category(tags):
    """Target group of an OSM object, or None (not a target or excluded)."""
    kind = next((tags[k] for k in KINDS if tags.get(k)), "")
    name = tags.get("name", "").lower()
    if kind in DENY or not name:
        return None
    if tags.get("shop") == "psychic" or re.search(r"astrolog|tarot|kartenleg|wahrsag|hellse|voyan|cartomant|médium|"
                                                  r"\bmedial|numerolog", name):
        return "Astrologie-/Tarot-Beratung"
    if tags.get("shop") in ("esoteric", "spiritual", "new_age", "occult", "crystal") or (
            tags.get("shop") and re.search(r"esot|ésot|spirit", name)):
        return "Esoterik-/Spiritualitätsgeschäft"
    if tags.get("healthcare") == "alternative" or re.search(r"reiki|schaman|chamani|sciaman|geistheil|lichtarbeit|"
                                                            r"spirit|chakra", name):
        return "Spiritueller Coach / Praxis"
    return None


def _first(tags, *keys):
    return next((tags[k].strip() for k in keys if tags.get(k, "").strip()), "")


def _domain(url):
    try:
        host = urllib.parse.urlsplit(url if "//" in url else "//" + url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def keys(row):
    """Dedup keys: company name, website domain, phone (last 9 digits), e-mail."""
    phone = re.sub(r"\D", "", row["Telefon"])[-9:]
    return {k for k in (("n", re.sub(r"[^a-z0-9]", "", row["Firmenname"].lower())), ("w", _domain(row["Website"])),
                        ("p", phone if len(phone) == 9 else ""), ("e", row["E-Mail"].lower())) if k[1]}


def parse(elements, today):
    """Overpass JSON elements -> (qualified rows, counters)."""
    canton, pending = {}, []
    for e in elements:  # loop output: id-only elements followed by their canton area
        if e["type"] == "area":
            canton.update({k: e.get("tags", {}).get("ISO3166-2", "") for k in pending})
            pending = []
        elif "tags" not in e:
            pending.append((e["type"], e["id"]))
    stats = {"Treffer": 0, "ohne CH-Kanton": 0, "keine Zielgruppe": 0, "ohne Kontakt": 0}
    rows = []
    for e in (x for x in elements if x["type"] != "area" and "tags" in x):
        stats["Treffer"] += 1
        t, kanton = e["tags"], canton.get((e["type"], e["id"]), "")
        if not kanton.startswith("CH-") or t.get("addr:country", "CH") != "CH":
            stats["ohne CH-Kanton"] += 1
            continue
        cat = category(t)
        if not cat:
            stats["keine Zielgruppe"] += 1
            continue
        phone, email = _first(t, "phone", "contact:phone", "contact:mobile"), _first(t, "email", "contact:email")
        if not (phone or email):
            stats["ohne Kontakt"] += 1
            continue
        closed = any(k.startswith(("disused:", "was:")) for k in t) or t.get("opening_hours") in ("closed", "off")
        rows.append({
            "Lead-ID": f"B2B-OSM-{e['type'][0]}{e['id']}", "Firmenname": t["name"], "Geschäftskategorie": cat,
            "Strasse": " ".join(filter(None, (t.get("addr:street", ""), t.get("addr:housenumber", "")))),
            "PLZ": t.get("addr:postcode", ""), "Ort": t.get("addr:city", ""), "Kanton": kanton[3:],
            "Website": _first(t, "website", "contact:website", "url"), "Telefon": phone, "E-Mail": email,
            "Ansprechpartner": "", "Quellen-URL": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
            "Erfassungsdatum": today, "Partnerschaftseignung": FIT[cat], "Partnerstatus": "potenzieller Vertriebspartner",
            "Kontaktstatus": "NEIN" if closed else "UNGEKLÄRT",
            "Begründung Kontaktstatus": "Geschäft laut OSM geschlossen" if closed else REASON})
    return rows, stats


def merge(existing, new):
    """Existing rows keep their ID and first capture date; empty fields get filled. Returns (rows, added)."""
    rows, added = [dict(r) for r in existing], 0
    for n in new:
        hit = next((r for r in rows if keys(r) & keys(n)), None)
        if hit is None:
            rows.append(n)
            added += 1
        else:
            for k, v in n.items():
                if v and not hit.get(k):
                    hit[k] = v
    rows.sort(key=lambda r: (PRIO.get("CH-" + r["Kanton"], 5), r["Kanton"], r["Firmenname"].lower()))
    return rows, added


def crm_rows(rows):
    """Rows for the existing CRM CSV import: never a contact permission, channel 'Keiner'."""
    return [{"id": r["Lead-ID"], "name": r["Firmenname"], "email": r["E-Mail"], "phone": r["Telefon"],
             "location": f"{r['PLZ']} {r['Ort']} / {r['Kanton']}".strip(), "source": "B2B-Recherche OpenStreetMap",
             "status": "Neu", "channel": "Keiner", "next": "", "permission": "false",
             "proof": f"{r['Quellen-URL']} (© OpenStreetMap-Mitwirkende), erfasst {r['Erfassungsdatum']}",
             "notes": (f"Potenzieller Vertriebspartner ({r['Geschäftskategorie']}), noch nicht kontaktiert. "
                       f"Kontaktstatus {r['Kontaktstatus']}: {r['Begründung Kontaktstatus']} "
                       f"Website: {r['Website'] or '-'}")} for r in rows]


def _query(url=OVERPASS):
    req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": research.USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read(5_000_000))["elements"]


def cycle(folder=FOLDER, query=None, now=None, robots=None):
    """One run. Never raises: each outcome ends up as one log line (no contact data in the log)."""
    now = now or datetime.now()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        state = folder / STATE
        if state.is_file() and now - datetime.fromisoformat(state.read_text().strip()) < SOURCE_MIN_AGE:
            return _log(folder, now, f"Quelle aktuell (letzte Abfrage {state.read_text().strip()}), keine neue Abfrage")
        if not (robots or research.allowed_by_robots)(OVERPASS):
            return _log(folder, now, "übersprungen: robots.txt von overpass.osm.ch verbietet den Abruf")
        new, stats = parse((query or _query)(), now.date().isoformat())
        path = folder / B2B_CSV
        rows, added = merge(read_csv(path) if path.is_file() else [], new)
        _replace(path, lambda p: write_csv(p, FIELDS, rows))
        _replace(folder / B2B_CRM, lambda p: write_csv(p, CRM_FIELDS, crm_rows(rows)))
        state.write_text(now.isoformat(timespec="seconds"))
        tel, mail = sum(bool(r["Telefon"]) for r in rows), sum(bool(r["E-Mail"]) for r in rows)
        both = sum(bool(r["Telefon"] and r["E-Mail"]) for r in rows)
        return _log(folder, now, f"OK {', '.join(f'{k}={v}' for k, v in stats.items())}, qualifiziert={len(new)}, "
                                 f"neu={added}, gesamt={len(rows)} (Telefon={tel}, E-Mail={mail}, beides={both})")
    except Exception as e:
        return _log(folder, now, f"FEHLER {type(e).__name__}: {e}")


def _log(folder, now, msg):
    line = f"{now.isoformat(timespec='seconds')} | {msg}"
    with open(folder / LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def report(folder=FOLDER, top=10):
    path, log = folder / B2B_CSV, folder / LOG
    rows = read_csv(path) if path.is_file() else []
    last = log.read_text(encoding="utf-8").splitlines()[-1] if log.is_file() else "noch kein Zyklus"
    lines = [f"Letzter Zyklus: {last}", f"B2B-Leads: {len(rows)} (Telefon {sum(bool(r['Telefon']) for r in rows)}, "
             f"E-Mail {sum(bool(r['E-Mail']) for r in rows)})", f"CSV: {path}", f"CRM-Import: {folder / B2B_CRM}", ""]
    lines += [f"{r['Kanton']:2} | {r['Firmenname']} | {r['Geschäftskategorie']} | {r['PLZ']} {r['Ort']} | "
              f"Tel {r['Telefon'] or '-'} | {r['E-Mail'] or '-'} | {r['Kontaktstatus']} | {r['Quellen-URL']}"
              for r in rows[:top]]
    return "\n".join(lines)


def run(interval=3600):
    while True:
        cycle()
        time.sleep(interval)


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if cmd == ["run"]:
        run()
    elif cmd == ["cycle"]:
        print(cycle())
    elif cmd == ["report"]:
        print(report())
    else:
        print(__doc__)
