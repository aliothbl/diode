#!/usr/bin/env bash
# P-10 A/B: std+nobranching+backpressure, CONCURRENCY=8 BATCH_SIZE=25, N runs
# per diode revision, A = before Connection: close, B = current HEAD.
# Usage: bench/tools/p10-ab.sh <rev_A> <N>
cd /home/alatour/repositories/netbox/diode || exit 1
A=$1; N=${2:-3}; HEAD_REF=$(git rev-parse --abbrev-ref HEAD)
trap 'git switch -q "$HEAD_REF"' EXIT
for rev in "$A" "$HEAD_REF"; do
  if [[ $rev == "$HEAD_REF" ]]; then git switch -q "$HEAD_REF"; else git switch -q --detach "$rev"; fi
  BENCH_COMPOSE_EXTRA="$PWD/bench/compose.nobranching.yaml $PWD/bench/compose.backpressure.yaml" \
    CONCURRENCY=8 BATCH_SIZE=25 bench/run.sh --version candidate --scenario std --runs "$N" 2>&1 | grep -E "==>|initial|reingest|update"
done
