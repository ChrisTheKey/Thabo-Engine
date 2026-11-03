"""Collect public purchase-intent signals from candidate URLs (stdlib only).

Usage: python -m lead_generation_ch_consumer.research urls.txt data/public_intent_signals.csv

Input is a list of public pages (one URL per line) found by a human or a permitted search.
For each URL: robots.txt is honoured, the page is fetched once, numerology-related
sentences are extracted, contact data and @handles are removed, the need is scored.
Output is anonymous (no names, no profiles, no contact data) and never enters the CRM.
No logins, no captcha bypass, no crawling beyond the given URLs.
"""
import hashlib
import html
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date
from pathlib import Path

from .leads import (EMAIL_RE, PHONE_RE, SIGNAL_FIELDS, prepare_signals, read_csv, score_signal, signal_category,
                    write_csv)

USER_AGENT = "ThaboResearchBot/1.0 (+manual review; respects robots.txt)"
TOPIC_RE = re.compile(r"[^.!?\n]*(numerolog|lebenszahl|geburtszahl|schicksalszahl)[^.!?\n]*[.!?]?", re.I)
HANDLE_RE = re.compile(r"@\w{2,}")
SWISS_RE = re.compile(r"\b(schweiz|zürich|zuerich|bern|basel|luzern|st\.? ?gallen|winterthur|aargau|"
                      r"thurgau|solothurn|graubünden|zug|schwyz|chf)\b", re.I)


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def allowed_by_robots(url, fetch=_fetch):
    parts = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(fetch(f"{parts.scheme}://{parts.netloc}/robots.txt").splitlines())
    except Exception:
        return False  # fail closed: unknown rules -> don't fetch
    return rp.can_fetch(USER_AGENT, url)


def page_text(raw_html):
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw_html)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def anonymize(text):
    return HANDLE_RE.sub("[…]", PHONE_RE.sub("[…]", EMAIL_RE.sub("[…]", text)))


def extract_signal(url, raw_html, today=None):
    """Best-scoring numerology sentence of a page as an anonymous signal, or None."""
    text = page_text(raw_html)
    sentences = [m.group(0).strip() for m in TOPIC_RE.finditer(text)]
    if not sentences:
        return None
    # A short post is scored as a whole, so an ad phrase in another sentence still marks it as provider.
    # shortcut: longer pages use their best sentence only; ad phrases elsewhere on the page are missed.
    best = text.strip() if len(text.strip()) <= 300 else max(sentences, key=lambda s: score_signal(s)[0])
    swiss = SWISS_RE.search(text) or urllib.parse.urlsplit(url).hostname.endswith(".ch")
    return {
        "signal_id": "SIG-" + hashlib.sha256(url.encode()).hexdigest()[:12].upper(),
        "platform": urllib.parse.urlsplit(url).hostname,
        "source_url": url,
        "found_at": (today or date.today()).isoformat(),
        "posted_at": "",
        "need_summary": anonymize(best)[:300],
        "product_fit": signal_category(score_signal(anonymize(best))[0]),
        # Only a hint; residence needs human verification (German text alone proves nothing).
        "geo_evidence": "Hinweis: CH-Bezug im Text/Domain – manuell prüfen" if swiss else "kein CH-Beleg",
        "anon_ref": "",
        "review_status": "ungeprüft",
    }


def collect(urls, fetch=_fetch, delay=2.0):
    signals, log = [], []
    for url in dict.fromkeys(u.strip() for u in urls if u.strip().startswith(("https://", "http://"))):
        if not allowed_by_robots(url, fetch):
            log.append((url, "robots.txt verbietet/unklar"))
            continue
        try:
            s = extract_signal(url, fetch(url))
        except Exception as e:
            log.append((url, f"Fehler: {type(e).__name__}"))
            continue
        log.append((url, "Signal" if s else "kein Numerologie-Bezug"))
        if s:
            signals.append(s)
        time.sleep(delay)
    ok, bad = prepare_signals(signals)
    log += [(s["source_url"], "abgelehnt: " + ", ".join(e)) for s, e in bad]
    return ok, log


def main(urls_file, out_csv):
    out = Path(out_csv)
    existing = read_csv(out) if out.is_file() else []
    new, log = collect(Path(urls_file).read_text(encoding="utf-8").splitlines())
    merged, _ = prepare_signals(existing + new)  # dedupe by URL, keep earlier entries
    write_csv(out, SIGNAL_FIELDS, merged)
    for url, status in log:
        print(f"{status:30} {url}")
    print(f"{len(log)} Quellen geprüft, {len(new)} neue Signale, {len(merged)} gesamt -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
