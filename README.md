# Thabo Engine – Numerology CRM & Lead-Modul

Lokales CRM für private Einzelkäufer persönlicher Numerologie-Reports (Fokus: deutschsprachige Schweiz) plus ein Python-Modul, das nur rechtmässig erhobene Opt-in-Leads CRM-fähig macht.

> **Öffentliches Repository.** Hier liegen nur Code und leere Vorlagen. Echte Leads, Kundendaten, Exporte (`*.csv`, `*.json`) und `.env` sind per `.gitignore` ausgeschlossen. Echte Daten gehören in den lokalen Ordner `data/`.

## Inhalt

| Pfad | Zweck |
|---|---|
| `numerology-crm/index.html` | Einzeldatei-CRM. Die Daten bleiben im `localStorage` des Browsers. CSV-Import/-Export, JSON-Backup, Filter nach Kontaktfreigabe |
| `lead_generation_ch_consumer/leads.py` | Validierung, Scoring, Deduplizierung, Kontaktfreigabe, CRM-CSV-Export |
| `lead_generation_ch_consumer/search_ch_adapter.py` | tel.search.ch-Adapter (nur Einzelabfragen, **keine Leadquelle**, siehe unten) |
| `lead_generation_ch_consumer/templates/` | Leere CSV-Vorlagen `opt_in_leads.csv`, `public_intent_signals.csv` |
| `tests/` | Unit-Tests, ausschliesslich mit synthetischen Daten |

Keine externen Abhängigkeiten, nur die Python-Standardbibliothek (getestet mit 3.14).

## Nutzung

```bash
python -m unittest discover -s tests -v       # Tests
mkdir data                                    # lokal, git-ignored
# Vorlagen nach data/ kopieren und mit echten Opt-in-Daten füllen
python -m lead_generation_ch_consumer data    # -> data/crm_import.csv
```

CRM: `numerology-crm/index.html` im Browser öffnen, dann **CSV importieren** und `data/crm_import.csv` wählen. Das CRM erkennt Dubletten selbst anhand von E-Mail und Telefon.

## Zwei getrennte Datensätze

**A. `public_intent_signals`** sind öffentlich sichtbare Nachfragebelege, z. B. Forenbeiträge wie „Was kostet eine persönliche Numerologie-Analyse?“. Sie sind anonym: Signale mit E-Mail-Adressen oder Telefonnummern werden abgelehnt. Sie gelangen **nie** ins CRM und sind **nie** kontaktierbar. Score 0–100:

| Score | Bedeutung |
|---|---|
| 90–100 | Preis- oder Bestellanfrage für eine persönliche Auswertung |
| 70–89 | Aktive Suche nach einem Anbieter |
| 40–69 | Persönliche Frage ohne erkennbare Kaufabsicht |
| 0–39 | Allgemeines Interesse oder gewerblicher Bedarf |

Der Score ist eine Recherche-Heuristik mit gespeicherten Gründen. Er ist keine Kaufwahrscheinlichkeit und keine Kontakterlaubnis.

**B. `opt_in_leads`** sind Personen, die ihre Kontaktdaten selbst für dieses Angebot angegeben haben. `can_contact()` liefert nur dann `true`, wenn alle folgenden Bedingungen erfüllt sind:

- Die Quelle ist eine nachweisbare Opt-in-Quelle (die Quellen-Liste des CRM).
- Einwilligungstext, Version und ISO-Zeitstempel sind vorhanden.
- `contact_permitted=true`, nicht widerrufen, nicht gesperrt.
- Für den gewählten Kanal liegen Kontaktdaten vor.

Ein Widerruf auf einer Dublette hat immer Vorrang. Widerrufene Leads werden als `Abgesagt`, Kanal `Keiner`, `permission=false` exportiert.

Pipeline → CRM-Status: `Kontakt freiwillig angemeldet`→Neu, `Kontaktaufnahme erlaubt`→Interessiert, `Angebot gesendet`, `Gekauft`, `Kein Interesse`/`Widerspruch / gesperrt`→Abgesagt. Die Stufen „Recherche-Signal“ und „Kaufinteresse öffentlich erkennbar“ bleiben bewusst ausserhalb des CRM.

## search.ch – Befund

Geprüft am 03.11.2026 anhand von https://search.ch/tel/api/help und https://search.ch/tel/api/terms:

- Endpoint `https://search.ch/tel/api/`, Key als Parameter `key`, Antwort im Atom-XML-Format. Fehler: 403 bei ungültigem Key, 400/401 bei fehlerhafter Anfrage.
- Kontingent: **1000 Abfragen pro Monat und Key**, maximal 20 Treffer pro Abfrage (Ziff. 3.4).
- Erlaubt: Einzelsuche und Anzeige von Einträgen, Anruferkennung, interner Gebrauch (Ziff. 4.1). Quellenangabe „Swisscom Directories AG“ ist Pflicht (Ziff. 4.2).
- Verboten: maschinelle Massenabfragen, der Aufbau von Adressdatenbanken, widerrechtliche Zwecke und die Weitergabe an Dritte (Ziff. 5.1). Konventionalstrafe: CHF 50 000 pro Verstoss.

**Fazit: Für die Suche nach Kaufinteressenten ungeeignet und unzulässig.** Ein Verzeichniseintrag belegt weder Interesse an Numerologie noch eine Einwilligung in Werbung. Einträge mit `tel:nopromo` („Wünscht keine Werbung“) sind nach UWG für Werbung tabu. Der Adapter ist nur für interaktive Einzelabfragen gedacht, z. B. um einen Anrufer zu erkennen, und darf nicht über Listen laufen.

### API-Key sicher hinterlegen

1. `lead_generation_ch_consumer/.env.example` nach `lead_generation_ch_consumer/.env` kopieren. Die `.env` ist git-ignored.
2. Den Key lokal eintragen, ohne ihn in einen Chat oder ins Repo zu kopieren. Bei dieser PowerShell-Variante erscheint er weder in der History noch auf dem Bildschirm:
   ```powershell
   $k = Read-Host -AsSecureString "SEARCH_CH_API_KEY"
   "SEARCH_CH_API_KEY=" + [Net.NetworkCredential]::new("", $k).Password | Set-Content -NoNewline lead_generation_ch_consumer\.env
   ```
3. Verbindung testen: `python -m lead_generation_ch_consumer.search_ch_adapter`. Das ist eine einzelne `count_only`-Abfrage nach Bäckereien in Bern, ohne private Einträge. Der Key erscheint nie in Ausgaben. Achtung: Die API-Antwort selbst enthält den Key im `<id>`-Feld, deshalb das Roh-XML nie loggen.

## Recht und Datenschutz

Das Modul ist auf das revDSG und das UWG ausgelegt (Art. 3 Abs. 1 lit. o: Massenwerbung nur mit Einwilligung; lit. u: Sterneintrag beachten). Ausgeschlossen sind: automatische Werbeanrufe oder -mails, unaufgeforderte Direktnachrichten, Deanonymisierung, Kontaktdaten-Anreicherung und erfundene Leads oder Einwilligungen. Einwilligungsfelder in späteren Formularen dürfen nie vorangekreuzt sein.

## Offen

- Automatisierte Recherche öffentlicher Kaufsignale (Quellenauswahl nach Nutzungsbedingungen) ist noch nicht implementiert. Ein Signal lässt sich bisher nur manuell in `data/public_intent_signals.csv` erfassen.
- Opt-in-Formular bzw. Landingpage: noch nicht gebaut.
- Das CRM speichert lokal im Browser. Für Teamzugriff braucht es Hosting mit Authentifizierung.
