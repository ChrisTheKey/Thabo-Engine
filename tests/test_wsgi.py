"""WSGI app (PythonAnywhere entry point) and the server-side intake switch."""
import io
import json
import os
import runpy
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

from lead_generation_ch_consumer import server as srv

ROOT = Path(__file__).resolve().parents[1]


def form(**kw):
    base = dict(first_name="Wsgi", last_name="Synthetisch", email="wsgi@example.invalid", phone="",
                location="", channel="E-Mail", product="Persönliches Numerologie-Sheet",
                request="Synthetisch: was kostet es?", consent=True,
                consent_version=srv.CONSENT_VERSION, website="", started_at=(time.time() - 30) * 1000)
    base.update(kw)
    return base


class WsgiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = srv.LeadStore(Path(self.tmp.name) / "w.sqlite3")
        self.app = srv.wsgi_app(self.store, srv.SpamGuard(limit=(1, 3600)))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, ctype="application/json", **extra):
        raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_TYPE": ctype,
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw), **extra}
        setup_testing_defaults(env)
        out = {}
        data = b"".join(self.app(env, lambda status, headers: out.update(status=status, headers=dict(headers))))
        return int(out["status"].split()[0]), out["headers"], data

    def leads(self):
        return self.store.db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    def test_pages_health_and_headers(self):
        code, headers, body = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"leadform", body)
        self.assertIn("max-age", headers["Strict-Transport-Security"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        for p in ["/../README.md", "/admin", "/leads.sqlite3", "/.env"]:
            self.assertEqual(self.call("GET", p)[0], 404, p)
        self.assertEqual(self.call("PUT", "/api/lead", form())[0], 405)

    def test_intake_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(json.loads(self.call("GET", "/health")[2]), {"ok": True, "leads_enabled": False})
            self.assertEqual(self.call("POST", "/api/lead", form())[0], 503)
        for value in ["0", "true", "yes", ""]:  # only the exact value "1" enables intake
            with mock.patch.dict(os.environ, {"THABO_LEADS_ENABLED": value}):
                self.assertEqual(self.call("POST", "/api/lead", form())[0], 503, value)
        self.assertEqual(self.leads(), 0)

    @mock.patch.dict(os.environ, {"THABO_LEADS_ENABLED": "1"})
    def test_intake_enabled_flow_and_real_ip(self):
        self.assertTrue(json.loads(self.call("GET", "/health")[2])["leads_enabled"])
        code, _, body = self.call("POST", "/api/lead", form(), HTTP_X_REAL_IP="203.0.113.1")
        self.assertEqual(code, 200)
        token = json.loads(body)["revoke_token"]
        # rate limit (1/h) is per real client IP, not per load-balancer address
        self.assertEqual(self.call("POST", "/api/lead", form(email="b@example.invalid"), HTTP_X_REAL_IP="203.0.113.1")[0], 429)
        self.assertEqual(self.call("POST", "/api/lead", form(email="c@example.invalid"), HTTP_X_REAL_IP="203.0.113.2")[0], 200)
        self.assertEqual(self.call("POST", "/api/lead", b"x" * (srv.MAX_BODY + 1))[0], 400)
        self.assertEqual(self.call("POST", "/api/lead", b"\xff\xfe", HTTP_X_REAL_IP="203.0.113.3")[0], 400)
        self.assertEqual(self.call("POST", "/api/revoke", {"token": token, "delete": True})[0], 200)
        self.assertEqual(self.leads(), 2)  # tombstone + c
        rows, _ = self.store.crm_rows()
        self.assertTrue(all(r["notes"].startswith(("E-Mail UNBESTÄTIGT", "GELÖSCHT")) for r in rows))

    def test_revoke_works_while_intake_disabled(self):
        with mock.patch.dict(os.environ, {"THABO_LEADS_ENABLED": "1"}):
            token = json.loads(self.call("POST", "/api/lead", form())[2])["revoke_token"]
        with mock.patch.dict(os.environ, {"THABO_LEADS_ENABLED": "0"}):
            self.assertEqual(self.call("POST", "/api/revoke", {"token": token})[0], 200)


class PythonAnywhereTemplateTest(unittest.TestCase):
    def test_template_starts_with_intake_off(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"THABO_DATA_DIR": d}, clear=False):
            os.environ.pop("THABO_LEADS_ENABLED", None)
            ns = runpy.run_path(str(ROOT / "deploy" / "pythonanywhere_wsgi.py"))
            self.assertTrue(callable(ns["application"]))
            self.assertEqual(os.environ["THABO_LEADS_ENABLED"], "0")
            self.assertTrue((Path(d) / "leads.sqlite3").is_file())
            store = next(c.cell_contents for c in ns["application"].__closure__
                         if isinstance(c.cell_contents, srv.LeadStore))
            store.db.close()


if __name__ == "__main__":
    unittest.main()
