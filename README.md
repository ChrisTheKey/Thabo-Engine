# Thabo Engine – Numerology CRM & Lead-Modul

Lokales CRM für private Einzelkäufer persönlicher Numerologie-Reports (Fokus: deutschsprachige Schweiz) plus ein Python-Modul, das nur rechtmässig erhobene Opt-in-Leads CRM-fähig macht.

> **Öffentliches Repository.** Hier liegen nur Code und leere Vorlagen. Echte Leads, Kundendaten, Exporte (`*.csv`, `*.json`) und `.env` sind per `.gitignore` ausgeschlossen. Echte Daten gehören in den lokalen Ordner `data/`.

## Inhalt

| Pfad | Zweck |
|---|---|
| `numerology-crm/index.html` | Einzeldatei-CRM. Die Daten bleiben im `localStorage` des Browsers. CSV-Import/-Export, JSON-Backup, Filter nach Kontaktfreigabe |
| `lead_generation_ch_consumer/leads.py` | Validierung, Scoring, Deduplizierung, Kontaktfreigabe, CRM-CSV-Export |
| `numerology-landing/` | Mobiloptimierte Landingpage mit Anfrageformular, Datenschutzhinweisen und Widerruf |
| `lead_generation_ch_consumer/server.py` | Backend für die Landingpage: Validierung, Einwilligungsnachweis, Spam-Schutz, SQLite ausserhalb des Repos, CRM-Export |
| `lead_generation_ch_consumer/research.py` | Sammelt anonyme öffentliche Kaufsignale aus vorgegebenen URLs (beachtet robots.txt) |
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

### Landingpage lokal betreiben

```bash
python -m lead_generation_ch_consumer.server serve --port 8080   # http://127.0.0.1:8080/
python -m lead_generation_ch_consumer.server export data/crm_import.csv
```

Die Daten liegen in `THABO_DATA_DIR`, standardmässig `~/.thabo-engine/leads.sqlite3`. Der Server verweigert den Start, wenn dieser Pfad innerhalb des Repositorys liegt.

Den Export danach im CRM über **CSV importieren** laden. Der Import:

- legt neue Leads an,
- setzt bereits vorhandene Leads auf gesperrt (`GESPERRT`: Status Abgesagt, Kanal Keiner, keine Freigabe),
- entfernt gelöschte Leads anhand der Lead-ID (`GELÖSCHT`).

Nach jedem Widerruf oder jeder Löschung also neu exportieren und importieren. Meldet sich eine Person nach einem Widerruf erneut an, bleibt sie im CRM gesperrt, bis sie dort manuell freigegeben wird.

**Ablauf Formular → CRM:**

1. **Lead-ID:** wird zufällig vergeben (`TN-` + 12 Hex-Zeichen).
2. **Validierung:** Pflichtfelder, E-Mail-Format, Telefon nur Schweizer Nummern (normalisiert auf `+41…`). Wer den Kanal Telefon wählt, muss eine Nummer angeben.
3. **Einwilligung:** Pflicht ist ein aktiver Haken (JSON `true`) und die aktuelle `CONSENT_VERSION`. Gespeichert werden Text, Version und UTC-Zeitstempel. Ändert sich der Text, Version in `server.py` **und** `numerology-landing/app.js` erhöhen; ein Test prüft die Übereinstimmung.
4. **Deduplizierung:** E-Mail ist eindeutig (Gross-/Kleinschreibung egal). Neue und doppelte Anfragen erhalten dieselbe Antwort, damit niemand herausfinden kann, ob eine Adresse gespeichert ist.
5. **Status und Score:** neu = „Kontakt freiwillig angemeldet“ (CRM: Neu). Der Interessensscore steht in den CRM-Notizen.
6. **Spam-Schutz:** Honeypot-Feld, Mindest-Ausfüllzeit 3 s, höchstens 5 Anfragen pro IP und Stunde (nur im Speicher, IPs werden nicht gespeichert), Body maximal 10 KB, nur `application/json`. Keine CORS-Header, daher sind fremde Origins blockiert.
7. **Widerruf und Löschung:** Nach dem Absenden erhält die Person einen Widerrufscode; gespeichert wird nur dessen Hash. Mit dem Code kann sie unter `#widerruf` ihre Einwilligung widerrufen oder ihre Daten löschen. Bei einer Löschung bleibt nur die Lead-ID mit Zeitstempel.

### Öffentliche Kaufsignale recherchieren

```bash
# urls.txt: öffentliche Beiträge, eine URL pro Zeile (z. B. Ergebnisse einer manuellen Suche)
python -m lead_generation_ch_consumer.research urls.txt data/public_intent_signals.csv
```

So arbeitet das Modul:

- Pro URL ein Abruf mit 2 s Pause. Nur wenn robots.txt es erlaubt; ist robots.txt nicht abrufbar, wird die Seite übersprungen.
- Keine Logins, kein Crawling über die Liste hinaus.
- Gespeichert wird der relevanteste Numerologie-Satz, anonymisiert (E-Mail, Telefon, @Handles entfernt), mit Score und Begründung.
- Der Schweiz-Bezug ist nur ein Hinweis. Jedes Signal startet als `ungeprüft` und muss manuell kontrolliert werden.
- Zusätzlich gelten die Nutzungsbedingungen der jeweiligen Plattform. Diese müssen **vor** dem Eintragen einer URL geprüft werden; das kann das Modul nicht.

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
- Erlaubt (Ziff. 4.1, Fassung November 2017, live geprüft am 03.11.2026): Anruferkennung, Suche und Anzeige von Einträgen, Abspeicherung *des Eintrags* in unternehmenseigenen Systemen, Nutzung für den unternehmensinternen Gebrauch. Quellenangabe „Swisscom Directories AG“ ist Pflicht (Ziff. 4.2).
- Verboten (Ziff. 5.1): alles, was Ziff. 4 nicht ausdrücklich nennt, insbesondere maschinelle Massenabfragen zur Erstellung oder Aktualisierung von Adressdatenbanken, die Weitergabe an Dritte und widerrechtliche Zwecke. Werbung oder Akquise wird in den Bedingungen nirgends erwähnt. Konventionalstrafe: CHF 50 000 pro Verstoss (Ziff. 5.3).

**Fazit: Für die Suche nach Kaufinteressenten ungeeignet und unzulässig.** Ein Verzeichniseintrag belegt weder Interesse an Numerologie noch eine Einwilligung in Werbung. Einträge mit `tel:nopromo` („Wünscht keine Werbung“) sind nach UWG für Werbung tabu. Der Adapter ist nur für interaktive Einzelabfragen gedacht, z. B. um einen Anrufer zu erkennen, und darf nicht über Listen laufen.

### API-Key sicher hinterlegen

Der Key wird mit DPAPI verschlüsselt ausserhalb des Repositorys gespeichert: `%APPDATA%\ThaboEngine\search_ch_key.dpapi`. Nur dasselbe Windows-Konto kann ihn entschlüsseln. In einer eigenen PowerShell eingeben, nicht im Chat. Die Eingabe bleibt verdeckt:

```powershell
Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"  # nötig, wenn PowerShell 5.1 aus PowerShell 7 gestartet wurde
$d = "$env:APPDATA\ThaboEngine"; New-Item -ItemType Directory -Force $d | Out-Null
Read-Host -AsSecureString "search.ch API-Key" | ConvertFrom-SecureString | Set-Content -NoNewline -Encoding ascii "$d\search_ch_key.dpapi"
```

Reihenfolge beim Laden: zuerst die Umgebungsvariable `SEARCH_CH_API_KEY`, dann die DPAPI-Datei, zuletzt die git-ignorierte `lead_generation_ch_consumer/.env` (Klartext, nur noch als Rückfall).

Verbindung testen: `python -m lead_generation_ch_consumer.search_ch_adapter`. Das ist eine einzelne `count_only`-Abfrage nach Bäckereien in Bern, ohne private Einträge. Der Key erscheint nie in Ausgaben. Achtung: Die API-Antwort selbst enthält den Key im `<id>`-Feld, deshalb das Roh-XML nie loggen.

## Qualifizierte Leads aus Interessenssignalen – Befund (TF-NUMEROLOGY-006, 03.11.2026)

Geprüft wurde dieser Ablauf: öffentliches Interessenssignal, dann Name und Telefon, dann search.ch-Werbesperre, dann CRM. Ergebnis:

| | Anzahl |
|---|---|
| Qualifizierte Leads importiert | **0** |
| Geprüfte Kandidaten | 0 (es gibt keine echten Signal- oder Lead-Daten) |
| Ausgeschlossene Kandidaten | 0 |

Deshalb wurde **kein** Import gebaut:

- **Keine Identität:** Interessenssignale sind absichtlich anonym. Vorname, Nachname, Telefon und E-Mail liessen sich nur durch nachträgliches Suchen von Kontaktdaten ergänzen. Das ist ausgeschlossen (siehe „Recht und Datenschutz“).
- **Vertrag:** Ziff. 4.1 erlaubt, einzelne Einträge intern zu speichern und intern zu nutzen. Verboten ist, das Verzeichnis maschinell abzufragen, um eine Lead- oder Adressdatenbank aufzubauen (Ziff. 5.1, CHF 50 000 pro Verstoss). Ob Akquiseanrufe als „unternehmensinterner Gebrauch“ gelten, lassen die Bedingungen offen. Ohne schriftliche Bestätigung ist das deshalb **nicht** gedeckt.
- **Fehlende Werbesperre ist keine Erlaubnis:** Ein fehlender Sterneintrag belegt weder Interesse noch eine Einwilligung. Für E-Mail-Werbung braucht es ohnehin eine Einwilligung (UWG Art. 3 Abs. 1 lit. o).

Fehlende Berechtigungen, falls dieser Weg je verfolgt werden soll:

1. eine schriftliche Erlaubnis der Swisscom Directories AG für die Speicherung im CRM und die kommerzielle Nutzung
2. eine rechtliche Prüfung, ob Personen anhand öffentlicher Beiträge identifiziert werden dürfen

Der zulässige Weg zu qualifizierten Leads ist das bestehende Opt-in-Formular (Datensatz B). Dort liefern die Personen Interesse, Kontaktdaten und Einwilligung selbst. Eine Prüfung der Werbesperre ist bei einer Einwilligung nicht nötig.

## Recherche Interessenssignale (TF-NUMEROLOGY-007, 03.11.2026)

Durchgeführt wurden 6 Websuchen (deutsch und englisch, mit Schweiz-Bezug, gutefrage.net, Reddit).

| | Anzahl |
|---|---|
| Signale mit konkretem Kaufinteresse an einem persönlichen Lebensweg-Report (Score ab 70) | **0** |
| Signale mit allgemeinem Interesse (Score 0–39), z. B. [„Was ist eure Lebenszahl?“](https://www.gutefrage.net/frage/was-ist-eure-lebenszahl), [„Engelszahlen und Lebenszahlen?“](https://www.gutefrage.net/frage/engelszahlen-und-lebenszahlen) | 2 |
| Kandidaten mit zulässigem Kontaktweg | **0** |
| Qualifizierte Leads und importierte CRM-Kontakte | **0** |

Die Treffer waren:

- fast nur Anbieterseiten, Rechner, Bücher und Spam-PDFs
- Fragen auf gutefrage.net von pseudonymen Nutzern, ohne Name und ohne Kontaktfreigabe

Eine Direktnachricht wäre eine unaufgeforderte Werbebotschaft, und ein Abgleich mit dem Telefonbuch wäre eine Deanonymisierung. Beides ist ausgeschlossen. Reddit sperrt den Zugriff des Suchwerkzeugs. Die Seiten wurden nicht automatisch abgerufen, weil die Nutzungsbedingungen von gutefrage.net nicht geprüft sind.

**Schluss:** Öffentliche Signale belegen Nachfrage. Sie sind aber kein Weg zu kontaktierbaren Leads. Kontaktierbare Leads entstehen nur über das Opt-in-Formular (PR #1), sobald es live ist.

## Lead-Sync-Dienst (TF-NUMEROLOGY-009)

Ein versteckter Hintergrundprozess läuft ohne Claude Code und ohne Admin-Rechte. Er übernimmt einmal pro Stunde die Opt-in-Leads aus der Lead-Datenbank. Quelle ist PythonAnywhere, wenn `PA_USERNAME` und `PA_API_TOKEN` in `lead_generation_ch_consumer/.env` stehen (gleicher Download wie beim Backup), sonst die lokale DB in `THABO_DATA_DIR`. Seit TF-NUMEROLOGY-010 recherchiert er zusätzlich in öffentlichen Feeds (siehe unten), fragt aber search.ch nie ab. Kontaktierbar sind nur Personen, die ihre Daten und ihre Einwilligung selbst eingegeben haben.

```powershell
.\lead_generation_ch_consumer\leadsync.ps1 START -AtLogon   # starten; -AtLogon startet den Dienst nach jedem Login neu (optional)
.\lead_generation_ch_consumer\leadsync.ps1 STOP             # beendet den Prozess UND entfernt den Autostart
.\lead_generation_ch_consumer\leadsync.ps1 STATUS           # tatsächlicher Prozesszustand und Autostart
.\lead_generation_ch_consumer\leadsync.ps1 REPORT           # letzter Zyklus, Anzahl Leads, CSV-Pfad
```

**Lead-Sammelordner** (`Lead-Sammelordner/`): git-ignoriert, nur für das eigene Windows-Konto zugänglich (ACL). Der Ordner bleibt nach STOP erhalten.

- `numerologie_leads.csv` enthält nur qualifizierte Leads:
  - Name
  - Telefon oder E-Mail (oder beides)
  - Interessensnachweis
  - erteilte Kontaktberechtigung
  - nicht gesperrt
- `crm_import.csv` ist der bestehende CRM-Export. Er wird im CRM über **CSV importieren** geladen. Das CRM speichert im Browser, deshalb kann der Dienst es nicht direkt befüllen.
- `zyklen.log` enthält eine Zeile pro Zyklus, ohne Personendaten.
- `quellen.txt` listet die RSS/Atom-Feeds für die Recherche. Du pflegst sie selbst. Trag nur Quellen ein, deren Nutzungsbedingungen du geprüft hast.
- `recherche_signale.csv` enthält anonyme Interessenssignale. Sie sind nie Leads und kommen nie ins CRM.
- `recherche_letzter_zyklus.txt` zeigt pro Quelle, ob sie gelesen oder übersprungen wurde, und warum.

### Anrufstatus (`Darf angerufen werden`)

Die Einwilligung im Formular gilt nur für den Kanal, den die Person selbst gewählt hat. Daraus folgt:

| Status | Bedingung |
|---|---|
| **JA** | Kontaktberechtigung erteilt, Kanal Telefon gewählt, Nummer angegeben |
| **NEIN** | Widerruf oder Sperre, keine Nummer, oder Einwilligung nur für E-Mail |
| **UNGEKLÄRT** | Kanal unbekannt. Wird nie automatisch zu JA |

Eine search.ch-Prüfung auf Sterneintrag ist bei einer ausdrücklichen Einwilligung für den Telefonkanal nicht massgebend und wird deshalb nicht gemacht. Das CRM hat keine eigene Spalte für den Anrufstatus. Der Status steht deshalb am Anfang der Notizen („Anruf: JA (…)“).

### Internet-Recherche – Befund (TF-NUMEROLOGY-010, 03.11.2026)

Der Dienst liest pro Stunde jeden Feed aus `quellen.txt`, aber nur, wenn robots.txt den Abruf erlaubt. Die Beiträge durchlaufen `research.collect`: robots.txt wird nochmals geprüft, Kontaktdaten werden entfernt, der Inhalt wird bewertet. Pro Feed werden höchstens 20 Beiträge gelesen, mit 2 s Pause dazwischen.

Ergebnis des ersten echten Zyklus: **5 Quellen, alle 5 übersprungen.**

- robots.txt verbietet den Abruf: gutefrage.net, Reddit, astrologie-forum.de, paranormal.de
- esoterikforum.at sperrt Bots (HTTP 403)
- esoterik-forum.de existiert nicht mehr (HTTP 410)

Daraus ergeben sich 0 Signale und 0 Leads.

### Neue Quellen (TF-NUMEROLOGY-011, 03.11.2026)

Geprüft wurden 12 weitere Quellen:

| Quelle | Ergebnis |
|---|---|
| Hashtag-Feeds auf swiss.social, mastodon.social (#numerologie, #lebenszahl) und chaos.social | **nutzbar**: robots.txt erlaubt den Abruf, die Feeds sind öffentlich und für maschinellen Abruf gedacht |
| #numerology auf mastodon.social | nutzbar, aber englisch, deshalb weggelassen |
| mastodon.online, spirit-forum.de, astrologie.ch | robots.txt verbietet den Abruf |
| feddit.org (HTTP 400), wer-weiss-was.de, esoterikforum.de (HTTP 404) | kein nutzbarer Feed |

Seit 011 bewertet der Dienst den Beitragstext direkt im Feed und ruft keine Beitragsseiten mehr ab. Das hat drei Gründe:

- Mastodon rendert die Seiten per JavaScript, im HTML steht kein Text.
- Es entstehen weniger Anfragen.
- Feed-Links können keine Anfragen ins Heimnetz auslösen (kein SSRF).

Beitrags-URLs enthalten Pseudonyme und kommen deshalb nicht ins Statusprotokoll.

Erster echter Zyklus (19:05):

- 4 Feeds gelesen, 30 Beiträge, davon 16 mit Numerologie-Bezug
- 12 eindeutige anonyme Signale: 11 mit Score 20 (allgemeines Thema), 1 mit Score 50
- **0 mit Kaufabsicht (Score ab 70), 0 mit Schweiz-Bezug**

Inhaltlich waren die Beiträge:

- französische Buch- und Anbieterwerbung
- rumänische und tschechische Nachrichten
- ein Skeptiker-Podcast und ein kritischer Esoterik-Artikel
- ein Werbebeitrag eines Tarot-Anbieters

Kein Konto sucht einen persönlichen Report, und keines hat eine Kontakterlaubnis gegeben. Ergebnis: **0 Interessenten, 0 Leads.**

### Käufer von Anbietern unterscheiden, Bluesky-Suche (TF-NUMEROLOGY-013, 03.11.2026)

**Kategorien:** `score_signal` erkennt jetzt Anbieter/Werbung und Nachrichten/Unterhaltung, **bevor** Kauf-Signale geprüft werden. Vorher galt „Jetzt meine Beratung buchen“ als Preisanfrage mit Score 95. Jedes Signal bekommt in `product_fit` eine Kategorie: Kaufinteresse, allgemeines Interesse, Anbieter/Werbung oder Nachrichten/Unterhaltung. Diese Signale wurden als Anbieter erkannt:

- Werbe-Imperative mit „!“
- „mein(e) …-Beratung/Buch/Kurs“
- Rabatt und Gutscheine
- 👉
- Shop-Links auf Numerologie-Seiten

„Ich möchte jetzt eine Analyse buchen“ bleibt Kaufinteresse. Kurze Beiträge (bis 300 Zeichen) werden als Ganzes bewertet.

**Neue Quellen:**

| Quelle | Ergebnis |
|---|---|
| Bluesky-Suche `api.bsky.app/xrpc/app.bsky.feed.searchPosts` | **nutzbar**: öffentlich, ohne Login, robots.txt erlaubt den Abruf. Die Developer Guidelines verbieten Spam und automatisierte Aktionen, die Nutzer benachrichtigen. Lesen schränken sie nicht ein. Die Nutzungsbedingungen (Stand 14.08.2025) enthalten keine Klausel gegen automatisierten Abruf. |
| `public.api.bsky.app` | HTTP 403 |
| Lemmy-Suche (feddit.org, lemmy.world) | 0 bis 1 Treffer, nicht eingebunden |

Gesucht werden die Käufer-Formulierungen „numerologie“, „lebenszahl“, „numerologie beratung“, „lebensweg analyse“ und „numerologie kosten“, jeweils deutsch, neueste zuerst. Das Anfragedatum (`posted_at`) kommt aus dem Feed oder der API.

**Echter Zyklus (19:27):**

- 14 Quellen, davon 9 gelesen
- 62 Beiträge, 43 mit Numerologie-Bezug
- 26 neue Signale, **0 mit Kaufinteresse**

Über alle 38 Signale: 35 allgemeines Interesse, 3 Anbieter/Werbung, 0 Kaufinteresse, 0 mit Schweiz-Bezug.

Die Stichprobe an den Originaltexten zeigte:

- kein Anbieter als Käufer eingestuft
- keine echte Kaufanfrage dabei
- typische Beiträge: „Meine Lebenszahl ist die 9“, Zahlendeutungen, Esoterik-Werbung, Verschwörungsposts

**Einschränkungen:**

- Bluesky-Signale speichern die Beitrags-URL mit der DID des Kontos, also ein Pseudonym. Gelöschte Beiträge bleiben in der lokalen Übersicht stehen, bis man sie manuell entfernt.
- Die Fediverse-Signale aus 011 wurden mit der alten Satzauswahl gespeichert und erscheinen teils als „allgemein“ statt als Nachrichten.

**Blocker:** Es gibt keine deutschsprachige Numerologie-Quelle, die einen automatisierten Abruf erlaubt **und** Kaufinteressenten mit Kontakterlaubnis liefert. Selbst ein echter Kaufinteressent auf Bluesky wäre kein Lead: Eine öffentliche Erwähnung ist keine Kontaktberechtigung, und eine Direktnachricht wäre eine unaufgeforderte Werbebotschaft. Die nutzbaren Fediverse-Feeds enthalten Anbieter, Skeptiker und Nachrichten. Selbst wenn es eine gäbe: Öffentliche Beiträge sind pseudonym und liefern nur anonyme Signale. Name und Kontaktdaten über search.ch zu ergänzen, wäre die verbotene Verknüpfung von Verzeichnisdaten mit Interessensprofilen.

Beide CSVs werden in jedem Zyklus vollständig und atomar aus der Datenbank neu geschrieben. Dadurch:

- entstehen keine Dubletten und keine halben Zeilen,
- verschwinden widerrufene Leads aus beiden Dateien,
- enthalten CSV und CRM-Import denselben qualifizierten Bestand.

Ein fehlgeschlagener Zyklus wird protokolliert, beendet den Dienst nicht und lässt die letzten guten Dateien stehen.

**Einschränkungen:**

- **Kein Neustart nach Absturz:** Die Aufgabenplanung ist ohne erhöhte Rechte gesperrt. Stürzt der Python-Prozess selbst ab, startet er deshalb erst beim nächsten Login oder START neu.
- **Stand heute 0 Leads:** Das Formular ist nicht live. Ausserdem erhält ohne Double-Opt-in keine Adresse eine Kontaktberechtigung (siehe „Livegang-Status und Blocker“).

## search.ch-Kontakte auf Interesse prüfen – nicht zulässig (TF-NUMEROLOGY-012, 03.11.2026)

Geprüft wurde dieser Ablauf: Kontakte über die search.ch-API beziehen, zu jeder Person im Netz Numerologie-Interesse suchen, bei Treffern als Lead speichern. **Ergebnis: nicht zulässig und nicht umgesetzt.** Es wurde keine search.ch-Abfrage gemacht.

1. **Vertrag:** Ziff. 4.1 erlaubt abschliessend Anruferkennung, Suche und Anzeige, das Speichern des Eintrags und den unternehmensinternen Gebrauch. Ziff. 5.1 verbietet alles andere, ausdrücklich auch „maschinelle Massenabfragen, beispielsweise zur Erstellung oder Aktualisierung von Adressdatenbanken“ (CHF 50 000 pro Verstoss, Ziff. 5.3). Die API braucht einen Suchbegriff. Ohne konkreten Anlass heisst „Kontakte beziehen“ deshalb, Namen oder Orte systematisch abzufragen. Genau das ist verboten. Profiling der Einträge nennt Ziff. 4 nicht.
2. **Identität:** Interesse zu einer Person im Netz zu finden, geht nur über eine Namenssuche und eine Zuordnung per Namensgleichheit. Diese ist als Identitätsnachweis ausgeschlossen. Öffentliche Beiträge sind zudem fast immer pseudonym. Eine belegte Identität ist so nie erreichbar.
3. **Datenschutz:** Interessen einzelner Privatpersonen verdeckt zu ermitteln und mit ihren Verzeichnisdaten zu verknüpfen, ist Profiling (Art. 5 lit. f DSG). Der Zweck ist für die Betroffenen nicht erkennbar (Art. 6 Abs. 3 DSG). Ein Sterneintrag ist ausserdem ein ausdrücklicher Werbewiderspruch.

| | Anzahl |
|---|---|
| Rechtmässig geprüfte Kontakte | 0 (der Bezug selbst ist schon unzulässig) |
| Nachweislich interessierte Personen / qualifizierte Leads | 0 / 0 |
| Erlaubte Werbeanrufe | 0 |

Zulässig bleibt nur die Gegenrichtung mit konkretem Anlass. Ein Beispiel: Eine Person meldet sich selbst, etwa über einen Anruf oder das Formular. Dann darf ihr Eintrag einzeln nachgeschlagen werden, also Anruferkennung oder Einzelsuche nach Ziff. 4.1.

## B2B-Partnersuche Schweiz (TF-NUMEROLOGY-014)

`lead_generation_ch_consumer/b2b.py` sucht potenzielle **Vertriebspartner** in drei Zielgruppen: Esoterik- und Spiritualitätsgeschäfte, Astrologie- und Tarot-Beratungen, spirituelle Coaches.

**Quelle:** OpenStreetMap über die Overpass-Instanz des Schweizer OSM-Vereins (`overpass.osm.ch`, nur Schweizer Daten, robots.txt erlaubt den Abruf). Die SOSM-Bedingungen verbieten automatisierte Nutzung nicht, behalten sich aber Limits vor. Deshalb:

- eine Abfrage von rund 20 s höchstens **einmal pro Tag**, obwohl der Dienst stündlich aufwacht
- Daten „© OpenStreetMap-Mitwirkende“ (ODbL), nur intern genutzt

Verworfene Alternativen:

- `overpass-api.de`: robots.txt sperrt `/api/`
- kumi.systems und private.coffee: zum Zeitpunkt der Prüfung nicht erreichbar
- search.ch: Massenabfragen sind vertraglich verboten
- local.ch und Zefix: nicht geprüft

**Ablauf und Filter:**

- Gesucht wird nach Laden-Typ (esoteric, psychic, crystal …) und nach Namensbegriffen auf Deutsch, Französisch und Italienisch. Kurze Begriffe brauchen Wortgrenzen, damit „Freikirche“ nicht als „Reiki“ und „Prévoyance“ nicht als „voyance“ zählt.
- **Schweiz-Filter:** Jeder Treffer muss in einer Kantonsfläche `CH-xx` liegen. So fallen etwa Liechtenstein-Einträge weg.
- **Ausgeschlossen:** Kirchen, Versicherungen, Bordelle, Gastronomie und Elektronik, ebenso Einträge ohne Telefon und ohne E-Mail.
- **Sortierung:** Bern, Zürich, Basel, Luzern, übrige Deutschschweiz, Westschweiz, Tessin.

**Dubletten** werden über Firmenname, Website-Domain, Telefon (letzte 9 Ziffern) und E-Mail erkannt. Bestehende Einträge behalten ID und Erfassungsdatum, leere Felder werden ergänzt.

**Status:**

- Alle Treffer gelten als *potenzieller Vertriebspartner*.
- Kontaktstatus ist **UNGEKLÄRT**, bei einem laut OSM geschlossenen Geschäft **NEIN**, aber **nie automatisch JA**. Öffentliche Geschäftskontakte sind keine Werbeerlaubnis. Vor einem Anruf den Sterneintrag manuell per search.ch-Einzelabfrage prüfen. Keine Massenwerbung (UWG Art. 3 Abs. 1 lit. o/u).

**Dateien** (Server: `~/thabo-b2b/data`, Rechte nur für den Besitzer). Die Privatkunden-Dateien bleiben unberührt.

- `numerology_b2b_leads.csv`: alle Felder
- `numerology_b2b_crm_import.csv`: Format des bestehenden CRM-Imports, mit `permission=false` und Kanal „Keiner“
- `b2b_zyklen.log`: eine Zeile pro Zyklus, ohne Kontaktdaten

**Betrieb auf dem Server:**

- `deploy/b2b/thabo-b2b.service` ist eine systemd-Benutzereinheit: kein Root, keine Ports, Neustart bei Absturz, CPU 20 %, RAM 256 MB.
- Damit der Dienst nach einem Server-Neustart startet, braucht der Benutzer „Linger“.
- Fiverr- und Jarvis-Dienste bleiben unberührt.

```sh
ssh fiverr '~/thabo-b2b/thabo-b2b.sh START'    # starten + Autostart
ssh fiverr '~/thabo-b2b/thabo-b2b.sh STOP'     # beenden + Autostart aus (läuft erst nach START wieder)
ssh fiverr '~/thabo-b2b/thabo-b2b.sh STATUS'
ssh fiverr '~/thabo-b2b/thabo-b2b.sh REPORT'   # Zahlen + erste zehn Firmen
scp fiverr:thabo-b2b/data/numerology_b2b_crm_import.csv .   # CRM-Datei sicher auf den Laptop, dann im CRM „CSV importieren“
```

**Stand 03.11.2026:** Der Dienst läuft auf `ubuntu-4gb-fsn1-1` als `thabo-b2b.service` mit Linger (Start nach Server-Neustart). Geprüft wurden:

- Absturz-Neustart per `kill -9`
- STOP (kein Wiederanlauf)
- genau eine Instanz

Die Fiverr- und Jarvis-Container waren vor und nach dem Deployment identisch: IDs, Startzeit, Neustart-Zähler und Ports.

**Laptop-Synchronisierung:** Hetzner ist die primäre Quelle. Der bestehende Windows-Dienst (`leadsync.ps1`) zieht in jedem Zyklus beim Login und danach stündlich `numerology_b2b_leads.csv` und `numerology_b2b_crm_import.csv` per `scp` in den Lead-Sammelordner.

- **Übertragung:** verschlüsselt über SSH, mit `-B`, also ohne jede Rückfrage.
- **Atomar:** Jede Datei wird zuerst in eine `.part`-Datei im selben Ordner geladen. Sie ersetzt die lokale Datei nur, wenn der Spaltenkopf stimmt und sie nicht weniger Zeilen hat. Bei Abbruch oder kaputter Kopie bleibt die lokale Datei unverändert.
- **Lückenlos:** Weil der Server die vollständige, entdoppelte Liste führt, holt der nächste Lauf nach einer Pause alles nach.
- **Getrennt:** Die Privatkunden-Dateien werden nicht berührt.
- **Keine B2B-Suche auf Windows**, also keine doppelte Recherche.

**Voraussetzung für unbeaufsichtigten Betrieb:** Der Schlüssel liegt im Windows-SSH-Agent. Die Passphrase wird nirgends gespeichert; der Agent bewahrt den Schlüssel DPAPI-geschützt im Benutzerprofil auf. Damit das nach einem Windows-Neustart ohne Eingabe weiterläuft, muss der Agent-Dienst automatisch starten. Das geht nur mit Admin-Rechten: `Set-Service ssh-agent -StartupType Automatic`. Fehlt der Schlüssel, meldet der Zyklus `B2B-Sync FEHLER`, ändert aber nichts lokal. Abhilfe ist ein erneutes `ssh-add`.

Die CRM-Importdatei wird nur bereitgestellt. Das CRM speichert im Browser und wird nicht automatisch verändert. Eine aktualisierte CSV-Datei ist also noch kein erfolgter Import. Importiert wird von Hand über **CSV importieren**.

## Recht und Datenschutz

Das Modul ist auf das revDSG und das UWG ausgelegt (Art. 3 Abs. 1 lit. o: Massenwerbung nur mit Einwilligung; lit. u: Sterneintrag beachten). Ausgeschlossen sind: automatische Werbeanrufe oder -mails, unaufgeforderte Direktnachrichten, Deanonymisierung, Kontaktdaten-Anreicherung und erfundene Leads oder Einwilligungen. Einwilligungsfelder in späteren Formularen dürfen nie vorangekreuzt sein.

## Hosting auf PythonAnywhere EU (vorbereitet, noch nicht live)

Lokaler Server (`serve`) und PythonAnywhere (`deploy/pythonanywhere_wsgi.py` → `server.wsgi_app`) verwenden dieselbe Anfragelogik `server.respond()`. Beide setzen dieselben Sicherheits-Header (CSP, HSTS, `nosniff`, `DENY`, `no-referrer`).

**Lead-Freigabe:** Die Lead-Erfassung ist serverseitig gesperrt, solange `THABO_LEADS_ENABLED` nicht genau `1` ist. Gesperrt bedeutet: `POST /api/lead` antwortet mit 503 und speichert nichts. `GET /health` meldet `{"ok": true, "leads_enabled": false}`, und die Landingpage sperrt dann das Formular. Widerruf und Löschung bleiben immer möglich. Die WSGI-Vorlage setzt standardmässig `0`.

Einrichtung (kostenloser Beginner-Account, keine Zahlungsangaben nötig):

1. **Anmeldung (manuell, nur durch den Betreiber):** Konto auf <https://eu.pythonanywhere.com/> anlegen bzw. sich anmelden. Für Backups unter *Account → API token* einen Token erzeugen und nur in die lokale, git-ignorierte Datei `lead_generation_ch_consumer/.env` eintragen (`PA_USERNAME=…`, `PA_API_TOKEN=…`).
2. In einer *Bash console*: `git clone https://github.com/ChrisTheKey/Thabo-Engine.git ~/Thabo-Engine`
3. *Web → Add a new web app → Manual configuration →* aktuelle Python-3-Version.
4. Die WSGI-Datei (`/var/www/<user>_eu_pythonanywhere_com_wsgi.py`) vollständig durch den Inhalt von `deploy/pythonanywhere_wsgi.py` ersetzen. `THABO_LEADS_ENABLED` bleibt `0`.
5. *Force HTTPS* aktivieren und *Reload* klicken. Danach prüfen: `https://<user>.eu.pythonanywhere.com/health` muss `"leads_enabled": false` melden.

Die Lead-Daten liegen dort in `~/.thabo-engine/` und damit ausserhalb des Repositorys. Dieser Ordner wird nicht ausgeliefert.

## Verschlüsseltes Backup

Für das Backup werden gpg (bei Git for Windows enthalten) und ein eigenes Schlüsselpaar benötigt. Der private Schlüssel bleibt nur auf dem Laptop. Zum Verschlüsseln genügt der öffentliche Schlüssel, eine Passphrase ist dafür nicht nötig.

```bash
gpg --quick-generate-key "Thabo Backup <eigene-adresse>" default default never   # einmalig
# in lead_generation_ch_consumer/.env: THABO_BACKUP_GPG_RECIPIENT=<eigene-adresse>
python -m lead_generation_ch_consumer.backup pull                     # PythonAnywhere -> ~/Documents/ThaboBackups
python -m lead_generation_ch_consumer.backup local ~/.thabo-engine/leads.sqlite3
python -m lead_generation_ch_consumer.backup restore-test ~/Documents/ThaboBackups/leads-<datum>.sqlite3.gpg
```

So arbeitet das Backup:

- Es prüft vor dem Verschlüsseln jede Kopie mit `integrity_check`. Ein unvollständiger Download wird bis zu dreimal wiederholt.
- Es behält die letzten 12 Backups.
- Es verweigert einen Backup-Ordner im Repository.
- Unverschlüsselte Kopien liegen nur kurz in einem temporären Ordner.

`restore-test` entschlüsselt ein Backup in einen temporären Ordner und prüft es. Es sollte nach jedem neuen Backup-Schlüssel und danach regelmässig laufen.

## Livegang-Status und Blocker

Stand: Nichts ist öffentlich bereitgestellt.

| Voraussetzung | Status |
|---|---|
| Verantwortliche Person (voller Name) und Postadresse in den Datenschutzhinweisen | **fehlt** (Platzhalter in `numerology-landing/index.html`) |
| Aufbewahrungsfrist | **fehlt** (Platzhalter) |
| PythonAnywhere-Konto und Anmeldung | **fehlt** (Schritt 1 oben, nur manuell) |
| Double-Opt-in per Bestätigungsmail | **fehlt**: Bis dahin wird jede Adresse im CRM-Export als `E-Mail UNBESTÄTIGT` ohne Kontaktfreigabe markiert |
| Backup-Schlüssel erzeugt, erster `pull` und `restore-test` mit echtem Konto | offen |
| Inhaltliche Prüfung der Produkttexte und des Preises | offen |
| WSGI-Anbindung, Lead-Sperre, Sicherheits-Header, Backup mit Wiederherstellungstest | erledigt und getestet |

`THABO_LEADS_ENABLED=1` darf erst gesetzt werden, wenn alle Zeilen mit **fehlt** erledigt sind. Bis dahin darf höchstens die Informationsseite mit gesperrtem Formular öffentlich sein.

Hinweise für den Betrieb:

- Hinter dem PythonAnywhere-Load-Balancer gilt das Rate-Limit pro echter Client-IP (`X-Real-IP`). IPs werden nicht gespeichert.
- Das CRM speichert lokal im Browser. Für Teamzugriff braucht es Hosting mit Authentifizierung.
