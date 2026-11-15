#!/usr/bin/env bash
# Interleaved A/B of the plugin: A=$1 (before) B=$2 (after), N pairs of std+nobranching c=4.
cd /home/alatour/repositories/netbox/diode || exit 1
A=$1; B=$2; N=${3:-3}; P=/home/alatour/repositories/netbox/diode-netbox-plugin
BR=$(git -C $P branch --show-current)
trap 'git -C $P switch -q "$BR"' EXIT
for i in $(seq 1 "$N"); do
  for rev in "$A" "$B"; do
    git -C $P switch -q --detach "$rev" || exit 1
    BENCH_COMPOSE_EXTRA=$PWD/bench/compose.nobranching.yaml CONCURRENCY=4 \
      bench/run.sh --version candidate --scenario std --runs 1 > bench/out/ab-last.log 2>&1
    f=$(ls -t bench/results/*-std-run1.json | head -1)
    bench/.venv/bin/python - "$f" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
tot = sum(p["entities"] for p in d["phases"]); el = sum(p["elapsed_s"] for p in d["phases"])
fl = max((p.get("states_after") or {}).get("FAILED", 0) for p in d["phases"])
print(sys.argv[1].split("/")[-1][:15], d["plugin_rev"], f"{tot/el:.2f} ent/s", "timeouts", d["apply_timeouts"], "FAILED", fl, flush=True)
EOF
  done
done
