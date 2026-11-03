#!/bin/sh
# Control the Thabo B2B partner search on the server: START | STOP | STATUS | REPORT
# From the laptop: ssh fiverr '~/thabo-b2b/thabo-b2b.sh STATUS'
set -eu
UNIT=thabo-b2b.service
DIR="$HOME/thabo-b2b"
case "${1:-}" in
  START)
    systemctl --user enable --now "$UNIT"
    echo "GESTARTET: $UNIT laeuft, Autostart nach Server-Neustart aktiv" ;;
  STOP)
    # disable removes the autostart, --now ends the process: nothing runs again until START.
    systemctl --user disable --now "$UNIT"
    echo "GESTOPPT: $UNIT beendet, Autostart deaktiviert. Daten bleiben in $DIR/data" ;;
  STATUS)
    echo "Dienst: $(systemctl --user is-active "$UNIT" || true) | Autostart: $(systemctl --user is-enabled "$UNIT" 2>/dev/null || echo disabled) | Linger: $(loginctl show-user "$(id -un)" -p Linger --value)"
    systemctl --user show "$UNIT" -p MainPID -p NRestarts -p ActiveEnterTimestamp ;;
  REPORT)
    cd "$DIR/app" && THABO_B2B_FOLDER="$DIR/data" python3 -m lead_generation_ch_consumer.b2b report ;;
  *)
    echo "Nutzung: $0 START|STOP|STATUS|REPORT" >&2; exit 2 ;;
esac
