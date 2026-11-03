"""Sync service: qualification, call status, both CSVs, dedup, feed research, errors. Synthetic data only."""
import csv
import io
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lead_generation_ch_consumer import backup, research, server as srv, sync_service as sync
from lead_generation_ch_consumer.leads import CRM_FIELDS


def row(**kw):
    base = {f: "" for f in CRM_FIELDS}
    base.update(id="TN-1", name="Erika Synthetisch", email="e@example.invalid", phone="+41790000000",
                status="Neu", proof="Kontaktformular 2026-11-03, Einwilligung v1: Lebensweg-Report", permission="true")
    base.update(kw)
    return base


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class QualifiedTest(unittest.TestCase):
    def test_contact_combinations_accepted(self):
        self.assertTrue(sync.qualified(row(email="")))          # name + phone
        self.assertTrue(sync.qualified(row(phone="")))          # name + email
        self.assertTrue(sync.qualified(row()))                  # both

    def test_unqualified_rejected(self):
        for bad in [dict(phone="", email=""), dict(name=""), dict(name="(ohne Namen)"), dict(permission="false"),
                    dict(status="Abgesagt"), dict(proof="")]:
            self.assertFalse(sync.qualified(row(**bad)), bad)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "Lead-Sammelordner"

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_matches_crm_and_never_duplicates(self):
        rows = [row(), row(id="TN-2", name="Max Synthetisch", email="", phone="+41780000000"),
                row(id="TN-3", permission="false", status="Abgesagt", name="Gesperrt Synthetisch")]
        for _ in range(2):  # a second cycle must not append duplicates
            leads = sync.publish(rows, "https://x.example.invalid/", self.folder)
        csv_rows = read(self.folder / sync.LEADS_CSV)
        self.assertEqual([r["Lead-ID"] for r in csv_rows], ["TN-1", "TN-2"])
        self.assertEqual(len(leads), 2)
        self.assertEqual(csv_rows[1]["E-Mail-Adresse"], "")  # missing optional field stays empty
        crm = read(self.folder / sync.CRM_CSV)
        qualified_in_crm = [r["id"] for r in crm if sync.qualified(r)]
        self.assertEqual(qualified_in_crm, [r["Lead-ID"] for r in csv_rows])  # same lead set
        self.assertIn("TN-3", [r["id"] for r in crm])  # blocked row still reaches the CRM to block it
        self.assertEqual(list(self.folder.glob("*.tmp")), [])
        self.assertIn("Gespeicherte Leads: 2", sync.report(self.folder))

    def test_call_status_follows_chosen_channel(self):
        self.assertEqual(sync.call_status(row(channel="Telefon"))[0], "JA")
        self.assertEqual(sync.call_status(row(channel="E-Mail"))[0], "NEIN")      # phone given, but consent is for e-mail
        self.assertEqual(sync.call_status(row(channel="Telefon", phone=""))[0], "NEIN")
        self.assertEqual(sync.call_status(row(channel="Telefon", permission="false"))[0], "NEIN")
        self.assertEqual(sync.call_status(row(channel="Fax"))[0], "UNGEKLÄRT")   # unclear never becomes JA
        sync.publish([row(channel="Telefon"), row(id="TN-2", channel="E-Mail")], "u", self.folder)
        leads = read(self.folder / sync.LEADS_CSV)
        self.assertEqual([l["Darf angerufen werden"] for l in leads], ["JA", "NEIN"])
        self.assertTrue(all(l["Begründung Anrufstatus"] for l in leads))
        crm = read(self.folder / sync.CRM_CSV)
        self.assertTrue(crm[0]["notes"].startswith("Anruf: JA"))

    def test_revoked_lead_disappears_from_csv(self):
        sync.publish([row()], "u", self.folder)
        sync.publish([row(permission="false", status="Abgesagt")], "u", self.folder)
        self.assertEqual(read(self.folder / sync.LEADS_CSV), [])


@mock.patch.object(backup, "ENV_FILE", Path("does-not-exist"))
class CycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "Lead-Sammelordner"
        self.data = Path(self.tmp.name) / "data"
        self.env = mock.patch.dict(os.environ, {"THABO_DATA_DIR": str(self.data), "PA_USERNAME": "",
                                                "PA_API_TOKEN": ""})
        self.env.start()
        self.net = mock.patch.object(research, "_fetch", side_effect=OSError("offline test"))  # no real network
        self.net.start()

    def tearDown(self):
        self.net.stop()
        self.env.stop()
        self.tmp.cleanup()

    def add_lead(self, db):
        store = srv.LeadStore(db)
        store.add(dict(first_name="Erika", last_name="Synthetisch", email="z@example.invalid", phone="",
                       location="", channel="E-Mail", product="Persönliches Numerologie-Sheet",
                       request="Lebensweg-Report, was kostet es?"))
        store.db.close()

    def test_no_source(self):
        self.assertIn("keine Quelle", sync.cycle(self.folder))
        self.assertEqual(read(self.folder / sync.LEADS_CSV), [])  # central CSV exists, header only
        self.assertIn("Gespeicherte Leads: 0", sync.report(self.folder))
        sync.publish([row()], "u", self.folder)
        sync.cycle(self.folder)  # source gone again: existing leads must survive
        self.assertEqual(len(read(self.folder / sync.LEADS_CSV)), 1)

    def test_local_db_unconfirmed_email_is_not_a_lead(self):
        self.add_lead(srv.data_dir() / "leads.sqlite3")
        line = sync.cycle(self.folder)
        self.assertIn("OK", line)
        self.assertIn("CRM-Zeilen=1 | qualifiziert=0", line)  # no double opt-in yet -> no permission
        self.assertEqual(len(read(self.folder / sync.CRM_CSV)), 1)
        self.assertEqual(read(self.folder / sync.LEADS_CSV), [])
        self.assertNotIn("example.invalid", (self.folder / sync.LOG).read_text(encoding="utf-8"))

    def test_pythonanywhere_source_and_errors_do_not_raise(self):
        good = Path(self.tmp.name) / "pa.sqlite3"
        self.add_lead(good)
        with mock.patch.dict(os.environ, {"PA_USERNAME": "synthuser", "PA_API_TOKEN": "synthetic-token"}):
            line = sync.cycle(self.folder, opener=lambda req, timeout: io.BytesIO(good.read_bytes()))
            self.assertIn("OK Quelle=https://synthuser.eu.pythonanywhere.com/", line)

            def down(req, timeout):
                raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO())
            line = sync.cycle(self.folder, opener=down)
            self.assertIn("FEHLER BackupError", line)
            self.assertNotIn("synthetic-token", (self.folder / sync.LOG).read_text(encoding="utf-8"))
        # a failed cycle leaves the last good CSVs in place
        self.assertEqual(len(read(self.folder / sync.CRM_CSV)), 1)


class ResearchTest(unittest.TestCase):
    """Fake web: host a allows bots and has a feed, host b forbids by robots.txt, host c answers 403."""
    RSS = ("<rss><channel><item><link>https://a.example.invalid/q/1</link></item>"
           "<item><link>https://a.example.invalid/q/2</link></item>"
           "<item><link>http://127.0.0.1:8080/admin</link></item></channel></rss>")  # off-host: must be ignored
    PAGES = {"https://a.example.invalid/q/1": "<p>Was kostet eine persönliche Numerologie Analyse? Mail: x@example.invalid</p>",
             "https://a.example.invalid/q/2": "<p>Ich suche jemanden für meine Lebenszahl Auswertung in Zürich.</p>"}

    def fetch(self, url):
        if url.endswith("/robots.txt"):
            return "User-agent: *\nDisallow: /" if "b.example" in url else "User-agent: *\nAllow: /"
        if "c.example" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO())
        return self.RSS if url.endswith("/feed") else self.PAGES[url]

    def test_feed_research_is_anonymous_dedupes_and_skips_forbidden_sources(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / sync.SOURCES).write_text("# Kommentar\nhttps://a.example.invalid/feed\n"
                                               "https://b.example.invalid/feed\nhttps://c.example.invalid/feed\n",
                                               encoding="utf-8")
            n, new, status = sync.research_cycle(folder, self.fetch, delay=0)
            self.assertEqual((n, len(new)), (3, 2))
            self.assertIn("robots.txt verbietet", status[1])
            self.assertIn("HTTP 403", status[2])
            self.assertFalse(any("127.0.0.1" in s for s in status))  # off-host link never fetched
            raw = (folder / sync.SIGNALS_CSV).read_text(encoding="utf-8")
            self.assertNotIn("x@example.invalid", raw)  # contact data never stored
            self.assertEqual(sync.research_cycle(folder, self.fetch, delay=0)[1], [])  # no duplicates

    def test_feed_with_entities_rejected(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><link>&a;</link></item></channel></rss>'
        with self.assertRaises(ValueError):
            sync.feed_links(bomb)
        self.assertEqual(len(sync.feed_links(self.RSS)), 3)  # parser returns all; research_cycle filters hosts

    def test_default_sources_file_created(self):
        with tempfile.TemporaryDirectory() as d:
            n, new, status = sync.research_cycle(Path(d), lambda url: (_ for _ in ()).throw(OSError()), delay=0)
            self.assertEqual(n, 5)
            self.assertTrue(all(s.startswith("übersprungen") for s in status))


if __name__ == "__main__":
    unittest.main()
