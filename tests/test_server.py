"""Landing backend tests: synthetic data only, temp DB outside the repo."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lead_generation_ch_consumer import server as srv


def form(**kw):
    base = dict(first_name="Erika", last_name="Synthetisch", email="erika@example.invalid",
                phone="", location="Bern / BE", channel="E-Mail",
                product="Persönliches Numerologie-Sheet",
                request="Was kostet ein persönliches Sheet für mich?",
                consent=True, consent_version=srv.CONSENT_VERSION, website="",
                started_at=(time.time() - 30) * 1000)
    base.update(kw)
    return base


class ValidationTest(unittest.TestCase):
    def test_valid(self):
        v = srv.validate_submission(form(email="Erika@Example.invalid", phone="079 000 00 00"))
        self.assertEqual(v["email"], "erika@example.invalid")
        self.assertEqual(v["phone"], "+41790000000")

    def test_phone_formats(self):
        for raw in ["+41 79 000 00 00", "0041790000000", "079/000.00.00"]:
            self.assertEqual(srv.normalize_phone(raw), "+41790000000")
        for raw in ["+49 151 0000000", "12345", "+41 79 000 00", "+41 09 000 00 00"]:
            with self.assertRaises(srv.ValidationError):
                srv.normalize_phone(raw)

    def test_rejections(self):
        bad = [dict(email="kein-at"), dict(email="a@b"), dict(first_name=""), dict(channel="SMS"),
               dict(channel="Telefon", phone=""), dict(product="Gratis"), dict(request=""),
               dict(consent=False), dict(consent="true"), dict(consent_version="alt"),
               dict(first_name="x" * 81), dict(last_name="a\x00b")]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(srv.ValidationError):
                srv.validate_submission(form(**kw))


class SpamGuardTest(unittest.TestCase):
    def test_guard(self):
        now = [1_000_000.0]
        g = srv.SpamGuard(limit=(2, 3600), clock=lambda: now[0])
        ok = dict(started_at=(now[0] - 10) * 1000)
        self.assertEqual(g.check("ip", dict(ok, website="spam.example")), "honeypot")
        self.assertEqual(g.check("ip", dict(started_at=now[0] * 1000)), "too_fast")
        self.assertEqual(g.check("ip", {}), "too_fast")  # missing timestamp
        self.assertEqual(g.check("ip", dict(started_at="abc")), "too_fast")
        self.assertIsNone(g.check("ip", ok))
        self.assertIsNone(g.check("ip", ok))
        self.assertEqual(g.check("ip", ok), "rate_limited")
        self.assertIsNone(g.check("other", ok))
        now[0] += 3601
        self.assertIsNone(g.check("ip", ok))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = srv.LeadStore(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def add(self, **kw):
        return self.store.add(srv.validate_submission(form(**kw)))

    def test_create_records_consent_and_score(self):
        status, token = self.add()
        self.assertEqual(status, "created")
        self.assertTrue(token)
        r = self.store.db.execute("SELECT * FROM leads").fetchone()
        self.assertRegex(r["lead_id"], r"^TN-[0-9A-F]{12}$")
        self.assertEqual(r["consent_text"], srv.CONSENT_TEXT)
        self.assertEqual(r["consent_version"], srv.CONSENT_VERSION)
        self.assertTrue(r["consent_at"])
        self.assertGreaterEqual(r["score"], 90)
        self.assertNotIn(token, json.dumps(dict(r)))  # only the hash is stored

    def test_unique_ids_and_dedupe(self):
        self.add()
        self.assertEqual(self.add(email="ERIKA@example.invalid")[0], "duplicate")
        self.add(email="zweite@example.invalid")
        ids = [r[0] for r in self.store.db.execute("SELECT lead_id FROM leads")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_crm_export_contactable(self):
        self.add()
        rows, rejected = self.store.crm_rows()
        self.assertFalse(rejected)
        self.assertEqual(rows[0]["permission"], "true")
        self.assertEqual(rows[0]["status"], "Neu")
        self.assertEqual(rows[0]["location"], "Bern / BE")
        self.assertIn("Interessensscore", rows[0]["notes"])
        self.assertTrue(rows[0]["notes"].startswith("E-Mail UNBESTÄTIGT"))

    def test_revoke_blocks(self):
        _, token = self.add()
        self.assertFalse(self.store.revoke("falsch"))
        self.assertTrue(self.store.revoke(token))
        r = self.store.crm_rows()[0][0]
        self.assertEqual((r["permission"], r["channel"], r["status"]), ("false", "Keiner", "Abgesagt"))
        self.assertTrue(r["notes"].startswith("GESPERRT"))

    def test_delete_erases_personal_data(self):
        _, token = self.add(phone="+41 79 000 00 00")
        self.assertTrue(self.store.revoke(token, delete=True))
        dump = json.dumps([dict(r) for r in self.store.db.execute("SELECT * FROM leads")])
        for pii in ["Erika", "Synthetisch", "example.invalid", "790000000", "Bern", "kostet"]:
            self.assertNotIn(pii, dump)
        rows, _ = self.store.crm_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["notes"].startswith("GELÖSCHT"))
        self.assertFalse(rows[0].get("email"))
        self.assertFalse(self.store.revoke(token))  # token is gone too

    def test_resignup_after_revoke_records_new_consent(self):
        _, token = self.add()
        self.store.revoke(token)
        status, token2 = self.add(request="Doch interessiert, bitte Angebot")
        self.assertEqual(status, "renewed")
        self.assertNotEqual(token, token2)
        self.assertEqual(self.store.crm_rows()[0][0]["permission"], "true")


class DataDirTest(unittest.TestCase):
    def test_refuses_repo_paths(self):
        for p in [srv.REPO_ROOT, srv.REPO_ROOT / "data"]:
            with mock.patch.dict(os.environ, {"THABO_DATA_DIR": str(p)}), self.assertRaises(RuntimeError):
                srv.data_dir()


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = srv.LeadStore(Path(cls.tmp.name) / "h.sqlite3")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cls.store, srv.SpamGuard(limit=(3, 3600))))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.db.close()
        cls.tmp.cleanup()

    def call(self, path, body=None, ctype="application/json"):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), e.headers

    def test_flow(self):
        code, body, headers = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"leadform", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.call("/app.js")[0], 200)
        self.assertEqual(self.call("/../README.md")[0], 404)

        code, body, _ = self.call("/api/lead", form(email="http@example.invalid"))
        self.assertEqual(code, 200)
        token = json.loads(body)["revoke_token"]
        # duplicate gives the same generic answer, without a token
        code, body, _ = self.call("/api/lead", form(email="http@example.invalid"))
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "revoke_token": None}))
        # honeypot: fake success, nothing stored
        self.assertEqual(self.call("/api/lead", form(email="bot@example.invalid", website="x"))[0], 200)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM leads WHERE email='bot@example.invalid'").fetchone())
        # validation, content-type, size
        self.assertEqual(self.call("/api/lead", form(consent=False))[0], 400)
        self.assertEqual(self.call("/api/lead", form(), ctype="text/plain")[0], 400)
        self.assertEqual(self.call("/api/lead", b"x" * (srv.MAX_BODY + 1))[0], 400)
        self.assertEqual(self.call("/api/lead", b"[1]")[0], 400)
        # revoke
        self.assertEqual(self.call("/api/revoke", {"token": "nope"})[0], 404)
        self.assertEqual(self.call("/api/revoke", {"token": token, "delete": True})[0], 200)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM leads WHERE email='http@example.invalid'").fetchone())


if __name__ == "__main__":
    unittest.main()
