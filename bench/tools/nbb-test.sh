#!/usr/bin/env bash
# Run the netbox-branching test suite (or a subset) from a checkout, inside the
# bench NetBox image (v4.7.0), the way its CI does: testing/configuration.py via
# NETBOX_CONFIGURATION + PYTHONPATH, Postgres and Redis on localhost.
# Usage: bench/tools/nbb-test.sh [test labels...]   (default: netbox_branching.tests)
set -euo pipefail
SRC=${BRANCHING_SRC:-/home/alatour/repositories/netbox/netbox-branching}
IMG=diode-bench/netbox:v4.7.0
P=nbb-test
LABELS=("${@:-netbox_branching.tests}")
# KEEP_DB=1 keeps Postgres/Redis between runs and passes --keepdb (the test
# database migrations take ~10 min); remove them with: docker rm -f nbb-test-pg nbb-test-redis
KEEP_DB=${KEEP_DB:-0}
cleanup() { docker rm -f $P-pg $P-redis >/dev/null 2>&1 || true; }
EXTRA=()
if [[ $KEEP_DB == 1 ]]; then
  EXTRA=(--keepdb)
else
  trap cleanup EXIT
  cleanup
fi
if ! docker inspect $P-pg >/dev/null 2>&1; then
  docker run -d --name $P-pg -e POSTGRES_USER=netbox -e POSTGRES_PASSWORD=netbox -e POSTGRES_DB=netbox \
    postgres:17-alpine >/dev/null
  docker run -d --name $P-redis --network container:$P-pg redis:7-alpine >/dev/null
fi
until docker exec $P-pg pg_isready -U netbox -q; do sleep 1; done
docker run --rm --network container:$P-pg \
  -v "$SRC/netbox_branching:/opt/netbox/venv/lib/python3.14/site-packages/netbox_branching:ro" \
  -v "$SRC/testing:/testing:ro" \
  -v "$(cd "$(dirname "$0")" && pwd)/nbb-testing:/nbb-testing:ro" \
  -v "$(cd "$(dirname "$0")" && pwd)/nbb-testing/local_settings.py:/opt/netbox/netbox/netbox/local_settings.py:ro" \
  -e NETBOX_CONFIGURATION=configuration -e PYTHONPATH=/nbb-testing \
  --entrypoint /opt/netbox/venv/bin/python -w /opt/netbox/netbox \
  $IMG manage.py test --parallel 1 "${EXTRA[@]}" "${LABELS[@]}"
