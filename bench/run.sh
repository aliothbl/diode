#!/usr/bin/env bash
# Diode end-to-end benchmark runner.
#
#   bench/run.sh --version baseline|candidate [--scenario smoke|std|large] [--runs N]
#
# baseline  = netboxlabs commits pinned below (git worktrees, see BASELINE_ROOT)
# candidate = this checkout + the sibling diode-netbox-plugin checkout
#
# Every run starts from empty volumes, so runs are independent. Knobs that
# are the same on both sides can be overridden with env vars (RATE_RPS,
# RATE_BURST, BATCH_SIZE, CONCURRENCY, PLUGIN_TIMEOUT, NETBOX_WORKERS).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DIODE_REPO="$(cd "$HERE/.." && pwd)"
NETBOX_ROOT="$(cd "$DIODE_REPO/.." && pwd)"

BASELINE_DIODE_SHA=519ccec
BASELINE_PLUGIN_SHA=8c3a3bb
BASELINE_ROOT="${BASELINE_ROOT:-$HERE/.baseline}"
VENV="${BENCH_VENV:-$HERE/.venv}"

VERSION=""; SCENARIO=smoke; RUNS=1; KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2;;
    --scenario) SCENARIO="$2"; shift 2;;
    --runs) RUNS="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done

case "$SCENARIO" in
  smoke) LOAD_ARGS=(--devices 10 --ifaces 2);;
  std)   LOAD_ARGS=(--devices 200 --ifaces 4);;
  large) LOAD_ARGS=(--devices 1000 --ifaces 8 --timeout 7200);;
  # netbox-branching scenarios: driven by branch_scenarios.py instead of
  # loadgen.py, always with the netbox-branching overlay.
  branch-*) LOAD_ARGS=()
            BRANCHING_SRC="${BRANCHING_SRC:-$NETBOX_ROOT/netbox-branching}";;
  *) echo "unknown scenario $SCENARIO" >&2; exit 2;;
esac

ensure_worktree() {  # repo sha dest
  if [[ ! -d "$3" ]]; then
    git -C "$1" worktree add -q --detach "$3" "$2"
  fi
  [[ "$(git -C "$3" rev-parse --short=7 HEAD)" == "$2" ]] || { echo "$3 is not at $2" >&2; exit 1; }
}

case "$VERSION" in
  baseline)
    DIODE_SRC="$BASELINE_ROOT/diode"; PLUGIN_SRC="$BASELINE_ROOT/diode-netbox-plugin"
    ensure_worktree "$DIODE_REPO" "$BASELINE_DIODE_SHA" "$DIODE_SRC"
    ensure_worktree "$NETBOX_ROOT/diode-netbox-plugin" "$BASELINE_PLUGIN_SHA" "$PLUGIN_SRC";;
  candidate)
    DIODE_SRC="$DIODE_REPO"; PLUGIN_SRC="$NETBOX_ROOT/diode-netbox-plugin";;
  *) echo "--version baseline|candidate" >&2; exit 2;;
esac

dirty() { [[ -n "$(git -C "$1" status --porcelain --untracked-files=no -- . ':!specs' ':!bench' 2>/dev/null)" ]] && echo "-dirty" || true; }
DIODE_REV="$(git -C "$DIODE_SRC" rev-parse --short=7 HEAD)$(dirty "$DIODE_SRC")"
PLUGIN_REV="$(git -C "$PLUGIN_SRC" rev-parse --short=7 HEAD)$(dirty "$PLUGIN_SRC")"

# ---- binaries (the deliverable): linux/amd64, static, with checksums ----
BIN_DIR="$HERE/out/bin/$VERSION-$DIODE_REV"
mkdir -p "$BIN_DIR"
echo "==> building diode-server $DIODE_REV into $BIN_DIR"
for svc in ingester reconciler auth; do
  (cd "$DIODE_SRC/diode-server" && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags "-s -w" -o "$BIN_DIR/$svc" "./cmd/$svc")
done
(cd "$BIN_DIR" && sha256sum ingester reconciler auth > SHA256SUMS && go version > GO_VERSION)

# ---- load generator venv (same SDK for both versions) ----
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install -e "$NETBOX_ROOT/diode-sdk-python" "psycopg[binary]" redis
fi

export DIODE_BIN_DIR="$BIN_DIR" DIODE_SRC PLUGIN_SRC
# NetBox image is built from the candidate plugin's docker dir for BOTH sides,
# so the NetBox build is identical; only the mounted plugin source differs.
export PLUGIN_DOCKER_DIR="$NETBOX_ROOT/diode-netbox-plugin/docker"
COMPOSE=(docker compose -f "$HERE/compose.yaml")
# Extra overlays (e.g. bench/compose.profile.yaml). Results from such runs are
# tagged and must not be compared with measured runs.
for extra in ${BENCH_COMPOSE_EXTRA:-}; do COMPOSE+=(-f "$extra"); done
# Tag results with the overlays used, so compare.py never mixes them with
# plain runs (e.g. "std+nobranching", "std+profile").
TAG=""; for extra in ${BENCH_COMPOSE_EXTRA:-}; do
  t="$(basename "$extra" .yaml)"; TAG="$TAG+${t#compose.}"
done

# netbox-branching from a checkout instead of the image's pip 1.2.0.
BRANCHING_REV="pip-1.2.0"
if [[ -n "${BRANCHING_SRC:-}" ]]; then
  export BRANCHING_SRC
  BRANCHING_REV="$(git -C "$BRANCHING_SRC" rev-parse --short=7 HEAD)$(dirty "$BRANCHING_SRC")"
  COMPOSE+=(-f "$HERE/compose.branching.yaml")
fi

# NetBox DB seed: snapshot of the migrated Postgres data dir, keyed by the
# plugin revision (its migrations are part of the schema), and by the
# netbox-branching revision when a checkout is mounted.
# Keyed by the content of the code that owns migrations (git tree hash), not
# by commit: a commit that only touches specs/ or bench/ must not force a new
# ~8 min migration boot.
tree() { git -C "$1" rev-parse --short=10 "HEAD:$2" 2>/dev/null || echo "$3"; }
SEED_KEY="plugin-$(tree "$PLUGIN_SRC" netbox_diode_plugin "$PLUGIN_REV")"
[[ -n "${BRANCHING_SRC:-}" ]] && SEED_KEY="$SEED_KEY-nbb-$(tree "$BRANCHING_SRC" netbox_branching "$BRANCHING_REV")"
SEED="$HERE/out/seed/$SEED_KEY/pgdata.tgz"
PGVOL=diode-bench-netbox-pgdata
reset_stack() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  docker volume rm -f "$PGVOL" >/dev/null
  docker volume create "$PGVOL" >/dev/null
}
restore_seed() {
  docker run --rm -v "$PGVOL:/data" -v "$(dirname "$SEED"):/seed:ro" alpine:3.20 \
    sh -c 'tar xzf /seed/pgdata.tgz -C /data'
}
if [[ ! -s "$SEED" ]]; then
  echo "==> no NetBox seed for plugin $PLUGIN_REV: first boot runs migrations (~8 min), then it is snapshotted"
  mkdir -p "$(dirname "$SEED")"
  reset_stack
  "${COMPOSE[@]}" up -d --build --wait --wait-timeout 1200 netbox >/dev/null
  "${COMPOSE[@]}" stop netbox netbox-postgres >/dev/null
  docker run --rm -v "$PGVOL:/data:ro" -v "$(dirname "$SEED"):/seed" alpine:3.20 \
    sh -c 'tar czf /seed/pgdata.tgz.tmp -C /data . && mv /seed/pgdata.tgz.tmp /seed/pgdata.tgz'
  reset_stack
fi

RESULTS="$HERE/results"; mkdir -p "$RESULTS"
TS="$(date +%Y%m%d-%H%M%S)"

for run in $(seq 1 "$RUNS"); do
  echo "==> [$VERSION diode=$DIODE_REV plugin=$PLUGIN_REV branching=$BRANCHING_REV] scenario=$SCENARIO run $run/$RUNS"
  reset_stack
  restore_seed
  "${COMPOSE[@]}" up -d --build --wait --wait-timeout 600 >/dev/null

  STATS="$(mktemp)"
  ( while true; do
      docker stats --no-stream --format '{{json .}}' $("${COMPOSE[@]}" ps -q) 2>/dev/null \
        | sed "s/^/$(date +%s.%N) /"
    done ) > "$STATS" &
  STATS_PID=$!

  OUT="$RESULTS/$TS-$VERSION-$SCENARIO-run$run.json"
  set +e
  if [[ "$SCENARIO" == branch-* ]]; then
    "$VENV/bin/python" "$HERE/branch_scenarios.py" "$SCENARIO" > "$OUT.load"
  else
    "$VENV/bin/python" "$HERE/loadgen.py" "${LOAD_ARGS[@]}" > "$OUT.load"
  fi
  RC=$?
  set -e
  kill "$STATS_PID" 2>/dev/null; wait "$STATS_PID" 2>/dev/null || true

  DUMP="$(mktemp)"
  "${COMPOSE[@]}" exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py nbshell \
    < "$HERE/netbox/dump_state.py" > "$DUMP" 2>/dev/null || true
  STATE="$(sed -n 's/^.*BENCH_STATE //p' "$DUMP" | tail -1)"
  # Full rows, for diffing two end states: zcat $OUT.rows.gz | jq ...
  sed -n 's/^.*BENCH_ROWS //p' "$DUMP" | tail -1 | gzip > "${OUT%.json}.rows.json.gz"
  # Reconciler-side symptoms of the apply timeout/resend problem (P-1).
  APPLY_TIMEOUTS="$(docker logs diode-bench-diode-reconciler-1 2>&1 | grep -c 'bulk-plan-apply.*timeout awaiting' || true)"
  # Risks of concurrent batches: Postgres deadlocks the plugin retried, and
  # anything the reconciler logged at ERROR.
  DEADLOCKS="$(docker logs diode-bench-netbox-1 2>&1 | grep -c 'deadlock on entity' || true)"
  RECONCILER_ERRORS="$(docker logs diode-bench-diode-reconciler-1 2>&1 | grep -c '"level":"ERROR"' || true)"
  # Deadlocks Postgres resolved in the reconciler's DB (P-9). With the retry
  # in dbstore/postgres they still happen but no longer surface as errors.
  DIODE_PG_DEADLOCKS="$(docker logs diode-bench-diode-postgres-1 2>&1 | grep -c 'deadlock detected' || true)"
  # Distinct reconciler ERROR messages and the statements Postgres reported in
  # deadlocks, kept with the result so a failed run can be diagnosed later.
  docker logs diode-bench-diode-reconciler-1 2>&1 | grep '"level":"ERROR"' \
    | sed -E 's/"time":"[^"]*",//; s/[0-9a-f]{8}-[0-9a-f-]{27}/<uuid>/g; s/\b[0-9]{2,}\b/<n>/g' \
    | sort | uniq -c | sort -rn | head -10 > "$OUT.errors" || true
  docker logs diode-bench-diode-postgres-1 2>&1 | grep -A3 'deadlock detected' \
    | grep -o -- '-- name: [A-Za-z]*' | sort | uniq -c >> "$OUT.errors" || true
  # NetBox-side failures (a whole bulk-plan-apply batch failing in the plugin
  # also yields one reconciler ERROR per entity): first line of each error.
  docker logs diode-bench-netbox-1 2>&1 | grep -E 'ERROR|Error:|Exception:|" 50[0-9] ' \
    | sed -E 's/[0-9a-f]{8}-[0-9a-f-]{27}/<uuid>/g; s/\b[0-9]{2,}\b/<n>/g' | cut -c1-300 \
    | sort | uniq -c | sort -rn | head -10 | sed 's/^/netbox: /' >> "$OUT.errors" || true
  rm -f "$DUMP"

  "$VENV/bin/python" - "$OUT" "$OUT.load" "$STATS" <<PY
import json, sys, collections
out, load, stats = sys.argv[1:4]
doc = {"version": "$VERSION", "diode_rev": "$DIODE_REV", "plugin_rev": "$PLUGIN_REV",
       "scenario": "$SCENARIO$TAG", "branching_rev": "$BRANCHING_REV", "run": $run, "rc": $RC, "apply_timeouts": int("${APPLY_TIMEOUTS:-0}" or 0),
       "plugin_deadlock_retries": int("${DEADLOCKS:-0}" or 0), "reconciler_errors": int("${RECONCILER_ERRORS:-0}" or 0), "diode_pg_deadlocks": int("${DIODE_PG_DEADLOCKS:-0}" or 0),
       "knobs": {k: __import__("os").environ.get(k) for k in
                 ["RATE_RPS","RATE_BURST","BATCH_SIZE","CONCURRENCY","PLUGIN_TIMEOUT","NETBOX_WORKERS"]}}
try: doc["error_summary"] = open(out + ".errors").read().splitlines()
except Exception: doc["error_summary"] = []
try: doc.update(json.load(open(load)))
except Exception as e: doc["load_error"] = repr(e)
try: doc["netbox_state"] = json.loads('''$STATE''')
except Exception: doc["netbox_state"] = None
cpu = collections.defaultdict(list); mem = collections.defaultdict(list)
def pct(s): return float(s.rstrip('%') or 0)
def mib(s):
    v = s.split('/')[0].strip(); u = {'KiB':1/1024,'MiB':1,'GiB':1024,'B':1/1048576}
    for k in sorted(u, key=len, reverse=True):
        if v.endswith(k): return float(v[:-len(k)]) * u[k]
    return 0.0
for line in open(stats):
    try: _, js = line.split(' ', 1); s = json.loads(js)
    except Exception: continue
    n = s["Name"].replace("diode-bench-", "").rsplit("-", 1)[0]
    cpu[n].append(pct(s["CPUPerc"])); mem[n].append(mib(s["MemUsage"]))
doc["resources"] = {n: {"cpu_avg_pct": round(sum(v)/len(v),1), "cpu_max_pct": round(max(v),1),
                        "mem_max_mib": round(max(mem[n]),1)} for n, v in cpu.items() if v}
json.dump(doc, open(out, "w"), indent=1)
for p in doc.get("phases", []):
    print(f"   {p['phase']:9s} {p.get('entities','?'):>6} ent  {p.get('elapsed_s','?'):>8}s  "
          f"{p.get('entities_per_s','?'):>8} ent/s  {p.get('states_after','')}")
PY
  rm -f "$OUT.load" "$STATS" "$OUT.errors"
  if [[ $KEEP -eq 0 ]]; then "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; fi
  [[ $RC -eq 0 ]] || { echo "loadgen failed (rc=$RC), see $OUT" >&2; exit $RC; }
done
