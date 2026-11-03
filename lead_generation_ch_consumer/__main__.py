"""Build CRM import + cleaned signal list from local (git-ignored) data files.

Usage: python -m lead_generation_ch_consumer [data_dir]
Reads  data_dir/opt_in_leads.csv and data_dir/public_intent_signals.csv (if present)
Writes data_dir/crm_import.csv and data_dir/public_intent_signals.scored.csv
"""
import sys
from pathlib import Path

from .leads import (CRM_FIELDS, SIGNAL_FIELDS, prepare_signals, read_csv,
                    to_crm_rows, write_csv)


def main(data_dir="data"):
    d = Path(data_dir)
    leads_path, signals_path = d / "opt_in_leads.csv", d / "public_intent_signals.csv"
    if leads_path.is_file():
        rows, rejected = to_crm_rows(read_csv(leads_path))
        write_csv(d / "crm_import.csv", CRM_FIELDS, rows)
        allowed = sum(r["permission"] == "true" for r in rows)
        print(f"crm_import.csv: {len(rows)} Leads, davon {allowed} kontaktierbar, {len(rejected)} abgelehnt")
        for lead, errs in rejected:
            print(f"  abgelehnt {lead.get('lead_id') or '?'}: {', '.join(errs)}")
    if signals_path.is_file():
        ok, bad = prepare_signals(read_csv(signals_path))
        write_csv(d / "public_intent_signals.scored.csv", SIGNAL_FIELDS, ok)
        print(f"Signale: {len(ok)} bewertet, {len(bad)} abgelehnt (nicht im CRM, nicht kontaktierbar)")
    if not (leads_path.is_file() or signals_path.is_file()):
        print(f"Keine Daten in {d}/ – Vorlagen liegen in lead_generation_ch_consumer/templates/")


if __name__ == "__main__":
    main(*sys.argv[1:2])
