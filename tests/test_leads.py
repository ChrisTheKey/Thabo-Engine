"""Tests use synthetic data only (example.invalid / fictional numbers)."""
import tempfile
import unittest
from pathlib import Path

from lead_generation_ch_consumer.leads import (CRM_FIELDS, can_contact, prepare_signals,
                                               read_csv, score_signal, signal_category, to_crm_rows,
                                               validate_opt_in, write_csv)


def lead(**kw):
    base = dict(lead_id="T1", name="Test Person", email="test1@example.invalid", phone="",
                preferred_channel="E-Mail", product="Persönliches Numerologie-Sheet",
                request="Bitte um Angebot", signup_source="Kontaktformular",
                consent_text="Ich willige ein, zu dieser Anfrage kontaktiert zu werden.",
                consent_version="1", consent_at="2026-11-03T10:00:00+01:00",
                contact_permitted="true", revoked="false",
                pipeline_status="Kontaktaufnahme erlaubt", next_contact="2026-11-10")
    base.update(kw)
    return base


class ScoringTest(unittest.TestCase):
    def test_tiers(self):
        self.assertGreaterEqual(score_signal("Was kostet eine persönliche Numerologie-Analyse für mich?")[0], 90)
        self.assertTrue(70 <= score_signal("Ich suche einen Anbieter für meine Lebenszahl-Auswertung")[0] < 90)
        self.assertTrue(40 <= score_signal("Was bedeutet meine Geburtszahl 7?")[0] < 70)
        self.assertLess(score_signal("Numerologie ist spannend")[0], 40)
        self.assertEqual(score_signal("Schönes Wetter heute")[0], 0)

    def test_business_not_prioritised(self):
        self.assertLess(score_signal("Wiederverkauf: was kostet Numerologie-Report für meine Kunden")[0], 40)

    def test_providers_and_news_never_buyers(self):
        providers = ["Jetzt meine Numerologie-Beratung buchen! Was kostet sie? Nur CHF 90 für dich.",
                     "Ich biete persönliche Lebenszahl-Analysen an, Termin buchen per DM.",
                     "Mein neues Buch über Numerologie ist da – Rabatt mit Gutschein LEBEN10",
                     "Ihr persönlicher Schutzengel auf Basis der Numerologie! beispiel.de/numerologie/schutzengel",
                     "Dein Geburtsdatum ist kein Zufall. 👉 Berechne deine Lebenszahl",
                     "Ce livre vous invite à explorer la numérologie. Découvrez vos nombres!"]
        for t in providers:
            self.assertEqual(signal_category(score_signal(t)[0]), "Anbieter/Werbung", t)
        for t in ["Im Podcast sprechen wir über Numerologie – alles Zahlenzauber?",
                  "📰 Artikel: Esoterische Finanz-Communities und Numerologie"]:
            self.assertEqual(signal_category(score_signal(t)[0]), "Nachrichten/Unterhaltung", t)
        for t in ["Was kostet eine persönliche Numerologie-Auswertung für mich?",
                  "Suche jemanden, der mir eine Lebenszahl-Analyse erstellt",
                  "Wer erstellt mir einen Numerologie-Report zu meinem Geburtsdatum?",
                  "Ich möchte jetzt eine persönliche Lebenszahl-Analyse buchen, was kostet das?"]:
            self.assertEqual(signal_category(score_signal(t)[0]), "Kaufinteresse", t)
        self.assertEqual(signal_category(score_signal("Numerologie ist spannend")[0]), "allgemeines Interesse")

    def test_reproducible_with_reasons(self):
        t = "Wo kann ich eine individuelle Numerologie-Analyse bestellen?"
        self.assertEqual(score_signal(t), score_signal(t))
        self.assertTrue(score_signal(t)[1])


class SignalTest(unittest.TestCase):
    def test_dedupe_and_reject_contact_data(self):
        sigs = [
            dict(signal_id="S1", source_url="https://forum.example.invalid/t/1", need_summary="Was kostet meine Numerologie-Analyse?"),
            dict(signal_id="S2", source_url="https://forum.example.invalid/t/1/", need_summary="Duplikat"),
            dict(signal_id="S3", source_url="https://forum.example.invalid/t/2", need_summary="Numerologie, ruf mich an 000 000 00 00"),
            dict(signal_id="S4", source_url="", need_summary="Numerologie"),
        ]
        ok, bad = prepare_signals(sigs)
        self.assertEqual([s["signal_id"] for s in ok], ["S1"])
        self.assertEqual({s["signal_id"] for s, _ in bad}, {"S3", "S4"})

    def test_signals_never_become_crm_rows(self):
        rows, rejected = to_crm_rows([dict(signal_id="S1", source_url="https://x.example.invalid", score="95")])
        self.assertEqual(rows, [])
        self.assertEqual(len(rejected), 1)


class ConsentTest(unittest.TestCase):
    def test_valid_opt_in_is_contactable(self):
        self.assertTrue(can_contact(lead()))

    def test_permission_checks(self):
        self.assertFalse(can_contact(lead(contact_permitted="false")))
        self.assertFalse(can_contact(lead(revoked="true")))
        self.assertFalse(can_contact(lead(consent_text="")))
        self.assertFalse(can_contact(lead(consent_at="gestern")))
        self.assertFalse(can_contact(lead(preferred_channel="Telefon")))  # no phone given
        self.assertFalse(can_contact(lead(signup_source="Telefonbuch")))
        self.assertFalse(can_contact(lead(pipeline_status="Widerspruch / gesperrt")))
        self.assertIn("signup_source ist keine nachweisbare Opt-in-Quelle",
                      validate_opt_in(lead(signup_source="search.ch")))

    def test_revocation_on_duplicate_wins(self):
        rows, _ = to_crm_rows([lead(), lead(lead_id="T2", email="TEST1@example.invalid", revoked="true")])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["permission"], r["channel"], r["status"]), ("false", "Keiner", "Abgesagt"))
        self.assertEqual(r["next"], "")

    def test_crm_row_matches_crm_schema(self):
        rows, rejected = to_crm_rows([lead()])
        self.assertFalse(rejected)
        self.assertEqual(set(rows[0]), set(CRM_FIELDS))
        self.assertEqual(rows[0]["permission"], "true")
        self.assertEqual(rows[0]["status"], "Interessiert")
        self.assertTrue(rows[0]["proof"])  # CRM import requires name, source, proof


class CsvTest(unittest.TestCase):
    def test_roundtrip_and_crm_header(self):
        rows, _ = to_crm_rows([lead(request='Angebot "Komplett", bitte')])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "crm_import.csv"
            write_csv(p, CRM_FIELDS, rows)
            self.assertEqual(p.read_text(encoding="utf-8").splitlines()[0], ",".join(CRM_FIELDS))
            self.assertEqual(read_csv(p), rows)

    def test_formula_injection_neutralised_phone_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            write_csv(p, ["a", "b", "c", "d"], [dict(a='=HYPERLINK("http://x.invalid")', b="+41790000000",
                                                      c="@SUM(1)", d="-1+cmd")])
            r = read_csv(p)[0]
            self.assertEqual((r["a"][0], r["b"], r["c"][0], r["d"][0]), ("'", "+41790000000", "'", "'"))

    def test_crm_header_matches_html(self):
        html = (Path(__file__).parents[1] / "numerology-crm" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const fields=" + str(CRM_FIELDS).replace(" ", ""), html)


if __name__ == "__main__":
    unittest.main()
