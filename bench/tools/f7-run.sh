#!/usr/bin/env bash
# F7: py-spy on NetBox workers during a std load (fork netbox-branching, no branch
# active), then the router microbenchmark on the same stack. Profiling run, not
# a throughput measurement.
# Usage: bench/tools/f7-run.sh <outdir> [spy_seconds]
cd /home/alatour/repositories/netbox/diode || exit 1
OUT=$1; SECS=${2:-60}; mkdir -p "$OUT"
PY=bench/.venv/bin/python
export BRANCHING_SRC=/home/alatour/repositories/netbox/netbox-branching
BENCH_COMPOSE_EXTRA=$PWD/bench/compose.profile.yaml CONCURRENCY=4 \
  bench/run.sh --version candidate --scenario std --runs 1 --keep > "$OUT/run.log" 2>&1 &
RUN=$!
# Start sampling once applies are flowing in the initial phase.
until docker exec diode-bench-diode-postgres-1 psql -U diode -d diode -tAc \
    "select count(*) from ingestion_logs where state=3" 2>/dev/null | awk '$1>100{ok=1} END{exit !ok}'; do
  sleep 2
  kill -0 $RUN 2>/dev/null || { echo "run ended before sampling"; exit 1; }
done
$PY bench/f7_router.py spy "$SECS" "$OUT" > "$OUT/spy-share.json"
wait $RUN; echo "run rc=$?" >> "$OUT/run.log"
$PY bench/f7_router.py micro > "$OUT/micro.json"
docker compose -p diode-bench down -v --remove-orphans > /dev/null 2>&1
cat "$OUT/spy-share.json" "$OUT/micro.json"
