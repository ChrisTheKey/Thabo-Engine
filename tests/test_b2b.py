"""B2B partner search: Swiss filter, classification, dedup, CSVs, contact status. Synthetic data only."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from lead_generation_ch_consumer import b2b
from lead_generation_ch_consumer.leads import read_csv


def el(i, kind="node", **tags):
    return {"type": kind, "id": i, "tags": tags}


ELEMENTS = [
    el(1, name="Sternenladen Synthetisch", shop="esoteric", phone="+41 31 000 00 00", **{"addr:postcode": "3000",
       "addr:city": "Bern"}),
    el(2, name="Tarot Praxis Synthetisch", shop="psychic", email="tarot@example.invalid"),
    el(3, name="Siete Chakras", amenity="brothel", phone="+41 27 000 00 00"),
    el(4, name="Freikirche Synthetisch", amenity="place_of_worship", phone="+41 44 000 00 00"),
    el(5, name="Mondlaterne Synthetisch", shop="esoteric", phone="+423 000 00 00"),  # Liechtenstein: no CH canton
    el(6, name="Reiki Raum Synthetisch", healthcare="alternative"),  # no contact
    el(7, "way", name="Sternenladen Synthetisch Filiale", shop="esoteric", phone="031 000 00 00"),  # same phone as 1
    el(8, name="Lichtarbeit Synthetisch", office="therapist", email="licht@example.invalid", disused="yes",
       **{"disused:shop": "esoteric"}),
    # canton loop output: id-only elements, then the canton area
    {"type": "node", "id": 2}, {"type": "area", "id": 1, "tags": {"ISO3166-2": "CH-ZH"}},
    {"type": "node", "id": 1}, {"type": "node", "id": 3}, {"type": "node", "id": 6}, {"type": "way", "id": 7},
    {"type": "area", "id": 2, "tags": {"ISO3166-2": "CH-BE"}},
    {"type": "node", "id": 4}, {"type": "node", "id": 8}, {"type": "area", "id": 3, "tags": {"ISO3166-2": "CH-GR"}},
]


class B2BTest(unittest.TestCase):
    def test_parse_filters_switzerland_targets_and_contacts(self):
        rows, stats = b2b.parse(ELEMENTS, "2026-11-03")
        self.assertEqual(stats, {"Treffer": 8, "ohne CH-Kanton": 1, "keine Zielgruppe": 2, "ohne Kontakt": 1})
        by = {r["Firmenname"]: r for r in rows}
        self.assertNotIn("Mondlaterne Synthetisch", by)  # outside Switzerland
        self.assertNotIn("Siete Chakras", by)
        self.assertEqual(by["Sternenladen Synthetisch"]["Geschäftskategorie"], "Esoterik-/Spiritualitätsgeschäft")
        self.assertEqual(by["Tarot Praxis Synthetisch"]["Geschäftskategorie"], "Astrologie-/Tarot-Beratung")
        self.assertEqual(by["Lichtarbeit Synthetisch"]["Kontaktstatus"], "NEIN")  # closed business
        self.assertTrue(all(r["Kontaktstatus"] in ("UNGEKLÄRT", "NEIN") for r in rows))  # never JA automatically
        self.assertTrue(all(r["Partnerstatus"] == "potenzieller Vertriebspartner" for r in rows))
        self.assertEqual(by["Sternenladen Synthetisch"]["Quellen-URL"], "https://www.openstreetmap.org/node/1")

    def test_merge_dedupes_and_orders_by_priority(self):
        rows, _ = b2b.parse(ELEMENTS, "2026-11-03")
        merged, added = b2b.merge([], rows)
        names = [r["Firmenname"] for r in merged]
        self.assertNotIn("Sternenladen Synthetisch Filiale", names)  # same phone -> duplicate
        self.assertEqual(added, 3)
        self.assertEqual([r["Kanton"] for r in merged], ["BE", "ZH", "GR"])  # Bern, Zürich, rest
        again, added2 = b2b.merge(merged, rows)
        self.assertEqual((len(again), added2), (3, 0))
        crm = b2b.crm_rows(merged)
        self.assertTrue(all(c["permission"] == "false" and c["channel"] == "Keiner" for c in crm))
        self.assertTrue(all(c["proof"] and c["source"] for c in crm))  # CRM import requires name, source, proof

    def test_cycle_writes_files_and_spares_the_source(self):
        with tempfile.TemporaryDirectory() as d:
            folder, calls = Path(d), []

            def query():
                calls.append(1)
                return ELEMENTS
            now = datetime(2026, 11, 3, 12, 0)
            line = b2b.cycle(folder, query, now, robots=lambda u: True)
            self.assertIn("qualifiziert=4, neu=3, gesamt=3", line)
            self.assertEqual(len(read_csv(folder / b2b.B2B_CSV)), 3)
            self.assertEqual(len(read_csv(folder / b2b.B2B_CRM)), 3)
            self.assertIn("Quelle aktuell", b2b.cycle(folder, query, now + timedelta(hours=1), robots=lambda u: True))
            self.assertEqual(len(calls), 1)  # no second query within 24 h
            b2b.cycle(folder, query, now + timedelta(hours=25), robots=lambda u: True)
            self.assertEqual((len(calls), len(read_csv(folder / b2b.B2B_CSV))), (2, 3))  # re-run adds no duplicates
            self.assertIn("B2B-Leads: 3", b2b.report(folder))
            self.assertNotIn("example.invalid", (folder / b2b.LOG).read_text(encoding="utf-8"))

    def test_robots_refusal_and_errors_are_logged_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("robots.txt", b2b.cycle(Path(d), lambda: ELEMENTS, robots=lambda u: False))

            def boom():
                raise TimeoutError("langsam")
            self.assertIn("FEHLER TimeoutError", b2b.cycle(Path(d), boom, robots=lambda u: True))


if __name__ == "__main__":
    unittest.main()
