# Template for /var/www/<user>_eu_pythonanywhere_com_wsgi.py on PythonAnywhere EU.
# Contains no secrets. Lead data goes to ~/.thabo-engine/ (outside the repo, not web-served).
import os
import sys

PROJECT = os.path.expanduser("~/Thabo-Engine")
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

# Keep "0" until operator details, privacy notice and the live checks are complete.
os.environ.setdefault("THABO_LEADS_ENABLED", "0")

from lead_generation_ch_consumer.server import LeadStore, SpamGuard, data_dir, wsgi_app  # noqa: E402

application = wsgi_app(LeadStore(data_dir() / "leads.sqlite3"), SpamGuard())
