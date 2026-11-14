"""netbox-branching scenarios for the Diode benchmark harness.

    python bench/branch_scenarios.py branch-merge-race     (run by run.sh)

Spec: aliothbl/netbox-branching SPEC-20261114-1736.

branch-merge-race (finding F2):
  1. create + provision a branch synchronously (nbshell, admin user);
  2. point the diode-netbox-plugin default branch at it and restart the
     reconciler, so every bulk-plan-apply carries X-NetBox-Branch;
  3. ingest a dataset; when MERGE_AT of it has settled, merge the branch
     (Branch.merge(), what MergeBranchJob runs) while batches are in flight;
  4. after the pipeline drains, compare the objects in the branch schema
     with main. Objects present in the branch but absent from main were
     written to the branch and never merged: that is F2.

Ingestion that reaches NetBox after the branch left READY gets HTTP 400
(finding F6); the FAILED count is reported too.

Prints one JSON document (same shape as loadgen.py, plus "branching").
"""

import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import loadgen  # noqa: E402

NETBOX = "diode-bench-netbox-1"
NETBOX_PG = "diode-bench-netbox-postgres-1"
RECONCILER = "diode-bench-diode-reconciler-1"
BRANCH_NAME = "bench-br"
MERGE_AT = float(os.environ.get("MERGE_AT", "0.4"))


def nbshell(code, timeout=1800):
    """Run Python in NetBox's nbshell; return the last 'BENCH ' JSON line."""
    # nbshell is an interactive console: a compound statement (try/except,
    # for...) only runs once a blank line closes it, and anything after it is
    # a syntax error that is silently skipped. Sending the whole script as a
    # single exec() line sidesteps the console's statement parsing.
    out = subprocess.run(
        ["docker", "exec", "-i", NETBOX, "/opt/netbox/venv/bin/python",
         "/opt/netbox/netbox/manage.py", "nbshell"],
        input=f"exec({code!r})\n", capture_output=True, text=True, timeout=timeout)
    # The interactive console prefixes output with ">>> " prompts.
    lines = [ln.split("BENCH ", 1)[1] for ln in out.stdout.splitlines() if "BENCH " in ln]
    if not lines:
        raise RuntimeError(f"nbshell failed: rc={out.returncode}\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}")
    return json.loads(lines[-1])


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", NETBOX_PG, "psql", "-U", "netbox", "-d", "netbox", "-tA", "-F", "\t", "-c", sql],
        capture_output=True, text=True, check=True)
    return [ln.split("\t") for ln in out.stdout.splitlines() if ln]


SETUP = f"""
import json
from django.contrib.auth import get_user_model
from netbox_branching.models import Branch
from netbox_diode_plugin.models import Setting
user = get_user_model().objects.get(username='admin')
b = Branch(name={BRANCH_NAME!r})
b.save(provision=False)
b.provision(user=user)
b.refresh_from_db()
s = Setting.objects.first() or Setting.objects.create()
s.branch_id = b.pk
s.save()
print('BENCH ' + json.dumps({{'id': b.pk, 'schema_id': b.schema_id, 'schema': b.schema_name, 'status': b.status}}))
"""

MERGE = f"""
import json, time, traceback
from django.contrib.auth import get_user_model
from netbox_branching.models import Branch
user = get_user_model().objects.get(username='admin')
b = Branch.objects.get(name={BRANCH_NAME!r})
t0 = time.time(); err = None
try:
    b.merge(user=user)
except Exception as e:
    err = f'{{type(e).__name__}}: {{e}}'
b.refresh_from_db()
print('BENCH ' + json.dumps({{'status': b.status, 'error': err, 'merge_s': round(time.time() - t0, 3),
                              'applied_changes': b.applied_changes.count()}}))
"""


def object_keys(schema):
    """Natural keys of the objects the dataset creates, in one schema."""
    q = {
        "device": f"SELECT name FROM {schema}.dcim_device",
        "interface": f"SELECT d.name || '/' || i.name FROM {schema}.dcim_interface i "
                     f"JOIN {schema}.dcim_device d ON d.id = i.device_id",
        "ipaddress": f"SELECT host(address) || '/' || masklen(address) FROM {schema}.ipam_ipaddress",
    }
    return {k: {r[0] for r in psql(sql)} for k, sql in q.items()}


def branch_merge_race():
    import argparse
    args = argparse.Namespace(devices=int(os.environ.get("DEVICES", "60")), ifaces=4, batch=50, seed=42,
                              offset=0, grpc="127.0.0.1:58081", pg_port=55432, redis_port=56379,
                              poll=0.5, timeout=1800)
    branch = nbshell(SETUP, timeout=600)
    subprocess.run(["docker", "restart", RECONCILER], check=True, capture_output=True)
    time.sleep(5)  # reconciler fetches the default branch at startup

    import grpc
    from netboxlabs.diode.sdk.diode.v1 import ingester_pb2_grpc
    stub = ingester_pb2_grpc.IngesterServiceStub(grpc.insecure_channel(args.grpc))
    md = [("authorization", f"Bearer {loadgen.fake_jwt()}")]
    pipe = loadgen.Pipeline(args)
    entities = loadgen.build_dataset(args.devices, args.ifaces, args.seed, "initial")

    merge = {}

    def merger():
        target = int(len(entities) * MERGE_AT)
        while sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL) < target:
            time.sleep(0.2)
        merge["started_at_settled"] = sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL)
        merge["started_s"] = round(time.monotonic() - t0, 3)
        merge.update(nbshell(MERGE))

    t0 = time.monotonic()
    th = threading.Thread(target=merger, daemon=True)
    th.start()
    phase = loadgen.run_phase("initial", entities, args, pipe, stub, md)
    th.join(timeout=1800)

    in_branch = object_keys(branch["schema"])
    in_main = object_keys("public")
    lost = {k: sorted(in_branch[k] - in_main[k]) for k in in_branch}
    return {
        "params": {"devices": args.devices, "ifaces": args.ifaces, "merge_at": MERGE_AT},
        "phases": [phase],
        "branching": {
            "branch": branch,
            "merge": merge,
            "objects_in_branch": {k: len(v) for k, v in in_branch.items()},
            "objects_in_main": {k: len(v) for k, v in in_main.items()},
            # F2: written to the branch, never merged into main.
            "lost_in_merge": {k: len(v) for k, v in lost.items()},
            "lost_examples": {k: v[:5] for k, v in lost.items()},
            "ingest_failed": phase.get("states_after", {}).get("FAILED", 0),
        },
    }


SCENARIOS = {"branch-merge-race": branch_merge_race}

if __name__ == "__main__":
    print(json.dumps(SCENARIOS[sys.argv[1]]()))
