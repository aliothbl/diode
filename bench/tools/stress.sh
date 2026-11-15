#!/usr/bin/env bash
# Stress loop for P-9: std, no branching, c=8, batch 25. Stops on first run with reconciler errors/deadlocks/FAILED.
# Usage: bench/out/stress.sh <max_runs> [stop_on_error=1]
cd /home/alatour/repositories/netbox/diode || exit 1
max=${1:-8}; stop=${2:-1}
for i in $(seq 1 "$max"); do
  BENCH_COMPOSE_EXTRA=$PWD/bench/compose.nobranching.yaml CONCURRENCY=8 BATCH_SIZE=25 \
    bench/run.sh --version candidate --scenario std --runs 1 > bench/out/stress-last.log 2>&1
  f=$(ls -t bench/results/*-std-run1.json | head -1)
  line=$(bench/.venv/bin/python - "$f" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
failed = 0
for p in d.get("phases") or []:
    failed = max(failed, (p.get("states_after") or {}).get("FAILED", 0))
print(f"{sys.argv[1].split('/')[-1]} rc={d.get('rc')} rec_err={d.get('reconciler_errors')} pg_deadlocks={d.get('diode_pg_deadlocks')} failed={failed} errs={len(d.get('error_summary') or [])}")
EOF
)
  echo "run $i: $line"
  if [ "$stop" = 1 ] && ! echo "$line" | grep -q 'rec_err=0 pg_deadlocks=0 failed=0 errs=0'; then echo "STOP: error found in $f"; exit 0; fi
done
[ "$stop" = 1 ] && echo "DONE: no error in $max runs" || echo "DONE: $max runs"
