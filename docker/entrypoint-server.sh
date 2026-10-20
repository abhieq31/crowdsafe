#!/bin/sh
# Live mode when a venue file is mounted, the demo otherwise.
set -e
VENUE="${CROWDSAFE_VENUE:-/data/venue.json}"
LOGS="${CROWDSAFE_LOG_DIR:-/data/logs}"
if [ "$(id -u)" = "0" ]; then
  # A folder mounted from the host is often owned by root; the event record must still be writable.
  mkdir -p "$LOGS" && chown -R crowdsafe "$LOGS" 2>/dev/null || true
  RUN="setpriv --reuid=crowdsafe --regid=crowdsafe --init-groups"
else
  RUN=""
fi
if [ -f "$VENUE" ]; then
  $RUN crowdsafe venue check "$VENUE" || echo "WARNING: fix the venue problems above before the event."
  exec $RUN crowdsafe serve --venue "$VENUE" --host 0.0.0.0 --port 8000 --log-dir "$LOGS" "$@"
fi
echo "No venue file at $VENUE: running the DEMO. Create one with  crowdsafe venue new  and put it at data/venue.json"
exec $RUN crowdsafe demo --host 0.0.0.0 --port 8000 "$@"
