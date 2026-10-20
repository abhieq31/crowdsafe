#!/bin/sh
# Live mode when a venue file is mounted, the demo otherwise.
set -e
VENUE="${RTB_VENUE:-/data/venue.json}"
LOGS="${RTB_LOG_DIR:-/data/logs}"
if [ "$(id -u)" = "0" ]; then
  # A folder mounted from the host is often owned by root; the event record must still be writable.
  mkdir -p "$LOGS" && chown -R roomtobreathe "$LOGS" 2>/dev/null || true
  RUN="setpriv --reuid=roomtobreathe --regid=roomtobreathe --init-groups"
else
  RUN=""
fi
if [ -f "$VENUE" ]; then
  $RUN roomtobreathe venue check "$VENUE" || echo "WARNING: fix the venue problems above before the event."
  exec $RUN roomtobreathe serve --venue "$VENUE" --host 0.0.0.0 --port 8000 --log-dir "$LOGS" "$@"
fi
echo "No venue file at $VENUE: running the DEMO. Create one with  roomtobreathe venue new  and put it at data/venue.json"
exec $RUN roomtobreathe demo --host 0.0.0.0 --port 8000 "$@"
