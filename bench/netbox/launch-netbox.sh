#!/bin/bash
# Same as the plugin's dev launcher minus --reload and the access log:
# a file watcher and per-request logging add noise to a throughput benchmark.
exec granian \
  --host "::" \
  --port "8080" \
  --interface "wsgi" \
  --no-ws \
  --workers "${GRANIAN_WORKERS:-4}" \
  --blocking-threads "${GRANIAN_BLOCKING_THREADS:-4}" \
  --respawn-failed-workers \
  --backpressure "${GRANIAN_BACKPRESSURE:-${GRANIAN_WORKERS:-4}}" \
  --log \
  --log-level "warning" \
  --working-dir "/opt/netbox/netbox/" \
  --static-path-route "/netbox/static" \
  --static-path-mount "/opt/netbox/netbox/static/" \
  --pid-file "/tmp/granian.pid" \
  "netbox.granian:application"
