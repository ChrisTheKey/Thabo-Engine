"""Minimal tel.search.ch API adapter (https://search.ch/tel/api/help).

Allowed use per API terms (Ziff. 4.1): single-entry lookup/display, caller identification.
Forbidden (Ziff. 5.1): automated bulk queries, building address databases, unlawful
purposes. A directory entry is NOT evidence of purchase interest or advertising consent,
so this adapter is NOT a lead source. Quota: 1000 queries/month/key (Ziff. 3.4).
Attribution required: "Swisscom Directories AG" (Ziff. 4.2).
"""
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

API_URL = "https://search.ch/tel/api/"
ATTRIBUTION = "Daten: Swisscom Directories AG"
NS = {"a": "http://www.w3.org/2005/Atom",
      "os": "http://a9.com/-/spec/opensearchrss/1.0/",
      "tel": "http://tel.search.ch/api/spec/result/1.0/"}
MIN_INTERVAL_S = 1.0  # shortcut: per-process throttle only; monthly quota is not tracked across runs


class SearchChError(Exception):
    pass


# Written by the PowerShell command in the README (ConvertFrom-SecureString = DPAPI, current Windows user).
DPAPI_FILE = Path(os.environ.get("APPDATA", Path.home())) / "ThaboEngine" / "search_ch_key.dpapi"


def _dpapi_unprotect(blob):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(blob, len(blob))
    src, out = Blob(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                                    ctypes.byref(out)):
        raise SearchChError("DPAPI-Entschlüsselung fehlgeschlagen (anderes Windows-Konto?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def load_api_key(env_file=Path(__file__).with_name(".env"), dpapi_file=DPAPI_FILE):
    """SEARCH_CH_API_KEY from the environment, else the DPAPI file, else the git-ignored .env."""
    key = os.environ.get("SEARCH_CH_API_KEY", "").strip()
    if not key and os.name == "nt" and Path(dpapi_file).is_file():
        hexblob = Path(dpapi_file).read_text(encoding="utf-8-sig").strip()
        try:
            key = _dpapi_unprotect(bytes.fromhex(hexblob)).decode("utf-16-le").strip()
        except ValueError:
            raise SearchChError("DPAPI-Datei ist beschädigt") from None
    if not key and Path(env_file).is_file():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "SEARCH_CH_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise SearchChError("SEARCH_CH_API_KEY nicht gesetzt (siehe README)")
    return key


class SearchChClient:
    def __init__(self, key=None, opener=urllib.request.urlopen):
        self._key = key or load_api_key()
        self._open = opener
        self._last = 0.0

    def __repr__(self):  # never leak the key
        return "SearchChClient(key=***)"

    def _get(self, params):
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        url = API_URL + "?" + urllib.parse.urlencode({**params, "key": self._key, "lang": "de"})
        try:
            with self._open(url, timeout=15) as r:
                return ET.fromstring(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            reason = ""
            try:
                reason = ET.fromstring(body).findtext(".//tel:errorReason", "", NS)
            except ET.ParseError:
                pass
            if e.code == 403:
                raise SearchChError(f"API-Key abgelehnt (403) {reason}".strip()) from None
            if e.code == 429:
                raise SearchChError("Rate Limit erreicht (429), später erneut versuchen") from None
            raise SearchChError(f"HTTP {e.code} {reason}".strip()) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise SearchChError(f"Netzwerkfehler: {getattr(e, 'reason', e)}") from None
        except ET.ParseError:
            raise SearchChError("Antwort ist kein gültiges Atom-XML") from None

    def test_connection(self):
        """One cheap documented query (count_only) on a business category; returns hit count."""
        root = self._get({"was": "Bäckerei", "wo": "Bern", "privat": 0, "count_only": 1})
        return int(root.findtext("os:totalResults", "0", NS))

    def lookup(self, was, wo=""):
        """Single interactive lookup (max 20 results). Includes the 'nopromo' marker
        (* = wishes no advertising). Do not loop this over lists."""
        root = self._get({"was": was, "wo": wo, "maxnum": 20})
        return [{
            "name": e.findtext("a:title", "", NS),
            "type": e.findtext("tel:type", "", NS),
            "city": e.findtext("tel:city", "", NS),
            # Docs define the element but not its value format: fail closed on presence.
            "nopromo": e.find("tel:nopromo", NS) is not None,
        } for e in root.findall("a:entry", NS)]


if __name__ == "__main__":
    try:
        n = SearchChClient().test_connection()
        print(f"search.ch OK: Testabfrage lieferte {n} Treffer. {ATTRIBUTION}")
    except SearchChError as e:
        print(f"search.ch FEHLER: {e}")
        raise SystemExit(1)
