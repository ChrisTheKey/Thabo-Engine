"""Research module (fake fetcher, no network) and public-repo hygiene checks."""
import re
import subprocess
import unittest
from datetime import date
from pathlib import Path

from lead_generation_ch_consumer import research, server
from lead_generation_ch_consumer.leads import EMAIL_RE, PHONE_RE

ROOT = Path(__file__).resolve().parents[1]
PAGE = """<html><head><script>var x='numerologie';</script></head><body>
<p>Hallo zusammen aus Zürich. Was kostet eine persönliche Numerologie-Analyse für mich?
Schreibt mir an someone@example.invalid oder 079 000 00 00, @userhandle.</p></body></html>"""


def fake_fetch(pages):
    def _f(url):
        if url not in pages:
            raise OSError("404")
        return pages[url]
    return _f


class ResearchTest(unittest.TestCase):
    def test_extract_anonymous_scored_signal(self):
        s = research.extract_signal("https://forum.example.invalid/t/1", PAGE, today=date(2026, 11, 3))
        self.assertGreaterEqual(research.score_signal(s["need_summary"])[0], 90)
        joined = " ".join(s.values())
        self.assertNotRegex(joined, EMAIL_RE)
        self.assertNotRegex(joined, PHONE_RE)
        self.assertNotIn("@userhandle", joined)
        self.assertEqual(s["review_status"], "ungeprüft")
        self.assertIn("manuell prüfen", s["geo_evidence"])

    def test_robots_and_dedupe(self):
        robots_ok = "User-agent: *\nAllow: /\n"
        pages = {
            "https://a.example.invalid/robots.txt": robots_ok,
            "https://a.example.invalid/t/1": PAGE,
            "https://a.example.invalid/t/2": "<p>Nur Wetter heute.</p>",
            "https://b.example.invalid/robots.txt": "User-agent: *\nDisallow: /\n",
            "https://b.example.invalid/t/1": PAGE,
            # c has no robots.txt -> fail closed
            "https://c.example.invalid/t/1": PAGE,
        }
        urls = ["https://a.example.invalid/t/1", "https://a.example.invalid/t/1", "https://a.example.invalid/t/2",
                "https://b.example.invalid/t/1", "https://c.example.invalid/t/1", "ftp://x", ""]
        ok, log = research.collect(urls, fetch=fake_fetch(pages), delay=0)
        self.assertEqual([s["source_url"] for s in ok], ["https://a.example.invalid/t/1"])
        statuses = dict(log)
        self.assertIn("robots", statuses["https://b.example.invalid/t/1"])
        self.assertIn("robots", statuses["https://c.example.invalid/t/1"])
        self.assertEqual(statuses["https://a.example.invalid/t/2"], "kein Numerologie-Bezug")


class PublicFilesTest(unittest.TestCase):
    def tracked_files(self):
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / p for p in out.splitlines()]

    def test_no_personal_data_or_secrets_in_repo(self):
        allowed_email = re.compile(r"@example\.invalid$|^noreply@anthropic\.com$", re.I)
        for path in self.tracked_files():
            if path.suffix not in {".py", ".html", ".js", ".md", ".csv", ".txt", ".example", ""}:
                continue
            text = path.read_text(encoding="utf-8")
            for m in EMAIL_RE.finditer(text):
                with self.subTest(file=path.name, email=m.group(0)):
                    self.assertRegex(m.group(0), allowed_email)
            for m in PHONE_RE.finditer(text):
                digits = re.sub(r"\D", "", m.group(0))
                with self.subTest(file=path.name, phone=m.group(0)):
                    self.assertTrue(set(digits[-7:]) <= {"0"}, "only 000-placeholder numbers allowed")
            self.assertNotRegex(text, r"SEARCH_CH_API_KEY\s*=\s*[\"']?[A-Za-z0-9_-]{8,}")  # literal key values

    def test_data_files_are_ignored(self):
        for p in ["data/x.csv", "crm_import.csv", "leads.sqlite3", "lead_generation_ch_consumer/.env"]:
            r = subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT)
            self.assertEqual(r.returncode, 0, p)

    def test_landing_consent_matches_backend(self):
        page = (ROOT / "numerology-landing" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "numerology-landing" / "app.js").read_text(encoding="utf-8")
        self.assertIn(server.CONSENT_TEXT, page)
        self.assertIn(f"CONSENT_VERSION = '{server.CONSENT_VERSION}'", js)
        consent = re.search(r'<input type="checkbox" id="consent"[^>]*>', page).group(0)
        self.assertNotIn("checked", consent)  # never pre-ticked
        for name in ["first_name", "last_name", "email", "phone", "location", "channel", "product", "request"]:
            self.assertIn(f'name="{name}"', page)


if __name__ == "__main__":
    unittest.main()
