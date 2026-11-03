"""search.ch adapter tests: fake HTTP, no network, synthetic key."""
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lead_generation_ch_consumer import search_ch_adapter as sc

PS = shutil.which("powershell") or shutil.which("pwsh")  # pwsh may be a Store alias subprocess cannot start

FAKE_KEY = "synthetic-test-key-123"
FEED = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:openSearch="http://a9.com/-/spec/opensearchrss/1.0/" xmlns:tel="http://tel.search.ch/api/spec/result/1.0/">
 <openSearch:totalResults>42</openSearch:totalResults>
 <entry><title>Beispiel AG</title><tel:type>Organisation</tel:type><tel:city>Bern</tel:city></entry>
 <entry><title>Muster, Test</title><tel:type>Person</tel:type><tel:city>Thun</tel:city><tel:nopromo>*</tel:nopromo></entry>
</feed>"""


def opener_returning(body=FEED, error=None, seen=None):
    def _open(url, timeout):
        if seen is not None:
            seen.append(url)
        if error:
            raise error
        return io.BytesIO(body)
    return _open


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://search.ch/tel/api/", code, "x", {}, io.BytesIO(body))


@mock.patch.object(sc, "MIN_INTERVAL_S", 0)
class AdapterTest(unittest.TestCase):
    def test_key_from_env_and_dotenv_and_missing(self):
        with mock.patch.dict(os.environ, {"SEARCH_CH_API_KEY": FAKE_KEY}):
            self.assertEqual(sc.load_api_key(env_file="nope"), FAKE_KEY)
        with mock.patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text(f'SEARCH_CH_API_KEY="{FAKE_KEY}"\n', encoding="utf-8")
            self.assertEqual(sc.load_api_key(env_file=env, dpapi_file="none"), FAKE_KEY)
            with self.assertRaises(sc.SearchChError):
                sc.load_api_key(env_file=Path(d) / "missing", dpapi_file="none")

    @unittest.skipUnless(os.name == "nt" and PS, "DPAPI/PowerShell nur unter Windows")
    def test_key_from_dpapi_file_written_by_powershell(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "k.dpapi"
            # Same cmdlet the README uses; plaintext only exists inside this test process.
            subprocess.run([PS, "-NoProfile", "-Command",
                            f"ConvertTo-SecureString '{FAKE_KEY}' -AsPlainText -Force | ConvertFrom-SecureString"
                            f" | Set-Content -NoNewline '{f}'"], check=True)
            self.assertNotIn(FAKE_KEY, f.read_text(encoding="utf-8-sig"))
            with mock.patch.dict(os.environ, {"SEARCH_CH_API_KEY": ""}):
                self.assertEqual(sc.load_api_key(env_file="none", dpapi_file=f), FAKE_KEY)
                f.write_text("zz", encoding="utf-8")
                with self.assertRaises(sc.SearchChError):
                    sc.load_api_key(env_file="none", dpapi_file=f)

    def test_key_never_in_repr_or_errors(self):
        c = sc.SearchChClient(FAKE_KEY, opener_returning(error=http_error(500)))
        self.assertNotIn(FAKE_KEY, repr(c))
        with self.assertRaises(sc.SearchChError) as cm:
            c.test_connection()
        self.assertNotIn(FAKE_KEY, str(cm.exception))

    def test_connection_count(self):
        seen = []
        c = sc.SearchChClient(FAKE_KEY, opener_returning(seen=seen))
        self.assertEqual(c.test_connection(), 42)
        self.assertIn("count_only=1", seen[0])
        self.assertIn("privat=0", seen[0])

    def test_lookup_parses_nopromo(self):
        res = sc.SearchChClient(FAKE_KEY, opener_returning()).lookup("Beispiel", "Bern")
        self.assertEqual([r["nopromo"] for r in res], [False, True])

    def test_errors(self):
        bad_key = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:tel="http://tel.search.ch/api/spec/result/1.0/">
        <tel:errorReason>invalid key</tel:errorReason></feed>"""
        for err, text in [(http_error(403, bad_key), "403"), (http_error(429), "Rate Limit"),
                          (urllib.error.URLError("offline"), "Netzwerkfehler")]:
            with self.assertRaises(sc.SearchChError) as cm:
                sc.SearchChClient(FAKE_KEY, opener_returning(error=err)).test_connection()
            self.assertIn(text, str(cm.exception))
        with self.assertRaises(sc.SearchChError):
            sc.SearchChClient(FAKE_KEY, opener_returning(body=b"<html>")).test_connection()


if __name__ == "__main__":
    unittest.main()
