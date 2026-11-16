#!/usr/bin/env bash
# Regression battery over the current code of diode, diode-netbox-plugin and netbox-branching:
# one pass over the scenarios that validated each fix, with a one-line verdict per run.
# Usage: bench/tools/regression.sh  (prints a summary; full logs per step in bench/out/regression-*.log)
cd /home/alatour/repositories/netbox/diode || exit 1
PY=bench/.venv/bin/python
NB=$PWD/bench/compose.nobranching.yaml
step() {  # step <name> <env...> -- <run.sh args...>
  local name=$1; shift; local envs=()
  while [[ $1 != -- ]]; do envs+=("$1"); shift; done; shift
  local start; start=$(date +%Y%m%d-%H%M%S)
  echo "== $name ($(date +%H:%M))"
  env "${envs[@]}" bench/run.sh --version candidate "$@" > "bench/out/regression-$name.log" 2>&1 || echo "   run.sh rc=$?"
  for f in $(ls bench/results/*.json | awk -v s="$start" -F/ '$NF >= s'); do
    $PY - "$f" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
b = d.get("branching") or {}
fl = max([(p.get("states_after") or {}).get("FAILED", 0) for p in d.get("phases") or []] + [0])
tot = sum(p.get("entities", 0) for p in d.get("phases") or [] if "elapsed_s" in p and not p.get("timeout"))
el = sum(p["elapsed_s"] for p in d.get("phases") or [] if not p.get("timeout"))
v = [f"nb={d.get('netbox_version')}", f"FAILED={fl}", f"timeouts={d.get('apply_timeouts')}",
     f"rec_err={d.get('reconciler_errors')}"]
if not b and el:
    v.append(f"{tot / el:.2f} ent/s")
if "lost_in_merge" in b:
    v.append(f"lost={sum(b['lost_in_merge'].values())} status={b['merge'].get('status')}")
if "missing_in_branch" in b:
    v.append(f"missing={sum(b['missing_in_branch'].values())} sync_s={b['sync'].get('sync_s')}")
if "merges" in b:
    v.append(f"kind={b['kind']} final={b['final_status']} merged_events={b['events'].get('merged')}")
if "late_change_missing_in_branch" in b:
    v.append(f"late_missing={b['late_change_missing_in_branch']}")
print("  ", sys.argv[1].split("/")[-1][:40], " ".join(v))
EOF
  done
}
step std-c4        BENCH_COMPOSE_EXTRA=$NB CONCURRENCY=4 -- --scenario std --runs 3
step stress-c8b25  BENCH_COMPOSE_EXTRA=$NB CONCURRENCY=8 BATCH_SIZE=25 -- --scenario std --runs 5
step sync-ingest   CONCURRENCY=4 BATCH_SIZE=25 SYNC_OBJECTS=2000 -- --scenario branch-sync-ingest --runs 3
step merge-race    CONCURRENCY=4 MERGE_RACE_TIMEOUT=300 -- --scenario branch-merge-race --runs 3
step double-update DOUBLE_MERGE_KIND=update -- --scenario branch-double-merge --runs 2
step double-create DOUBLE_MERGE_KIND=create -- --scenario branch-double-merge --runs 1
step watermark     X=1 -- --scenario branch-sync-watermark --runs 2
echo "== done ($(date +%H:%M))"
