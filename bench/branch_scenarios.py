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

# The merge shell boots Django first (several seconds) and then waits for
# GO_FILE, so the merge starts at the chosen point of the load instead of
# whenever the shell finishes booting.
GO_FILE = "/tmp/bench-merge-go"
MERGE = f"""
import json, os, time
from django.contrib.auth import get_user_model
from netbox_branching.models import Branch
user = get_user_model().objects.get(username='admin')
while not os.path.exists({GO_FILE!r}):
    time.sleep(0.02)
b = Branch.objects.get(name={BRANCH_NAME!r})
changes_at_go = b.get_unmerged_changes().count()
t0 = time.time(); err = None
try:
    b.merge(user=user)
except Exception as e:
    err = f'{{type(e).__name__}}: {{e}}'
b.refresh_from_db()
print('BENCH ' + json.dumps({{'status': b.status, 'error': err, 'merge_s': round(time.time() - t0, 3),
                              'changes_at_go': changes_at_go,
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

    merge_result = {}
    shell = threading.Thread(target=lambda: merge_result.update(nbshell(MERGE)), daemon=True)
    shell.start()  # boots now, merges on GO_FILE

    def merger():
        target = int(len(entities) * MERGE_AT)
        while sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL) < target:
            time.sleep(0.1)
        subprocess.run(["docker", "exec", NETBOX, "touch", GO_FILE], check=True)
        merge["started_at_settled"] = sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL)
        merge["started_s"] = round(time.monotonic() - t0, 3)
        shell.join(timeout=1800)
        merge.update(merge_result)

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
            "objectchanges_in_branch": int(psql(f"SELECT count(*) FROM {branch['schema']}.core_objectchange")[0][0]),
            # F2: written to the branch, never merged into main.
            "lost_in_merge": {k: len(v) for k, v in lost.items()},
            "lost_examples": {k: v[:5] for k, v in lost.items()},
            "ingest_failed": phase.get("states_after", {}).get("FAILED", 0),
        },
    }


def branch_double_merge():
    """F1: two merges of the same READY branch at the same time.

    Ingest a dataset into the branch and let it drain, then boot two merge
    shells (two "workers") and release both at once. Report what each merge
    returned, the branch status at the end, and whether main received the
    changes once, twice or not at all.
    """
    import argparse
    args = argparse.Namespace(devices=int(os.environ.get("DEVICES", "20")), ifaces=4, batch=50, seed=42,
                              offset=0, grpc="127.0.0.1:58081", pg_port=55432, redis_port=56379,
                              poll=0.5, timeout=1800)
    # create: the branch holds only CREATEs (the 2nd merge hits unique keys).
    # update: objects are created in main first, so the branch holds only
    #         UPDATEs and nothing stops the 2nd merge from applying them again.
    kind = os.environ.get("DOUBLE_MERGE_KIND", "create")

    import grpc
    from netboxlabs.diode.sdk.diode.v1 import ingester_pb2_grpc
    stub = ingester_pb2_grpc.IngesterServiceStub(grpc.insecure_channel(args.grpc))
    md = [("authorization", f"Bearer {loadgen.fake_jwt()}")]
    pipe = loadgen.Pipeline(args)

    def ingest(phase_name):
        ents = loadgen.build_dataset(args.devices, args.ifaces, args.seed, phase_name)
        return loadgen.run_phase(phase_name, ents, args, pipe, stub, md)

    phases = []
    if kind == "update":
        phases.append(ingest("initial"))  # into main: no default branch yet
    branch = nbshell(SETUP, timeout=600)
    subprocess.run(["docker", "restart", RECONCILER], check=True, capture_output=True)
    time.sleep(5)
    phases.append(ingest("update" if kind == "update" else "initial"))
    main_oc_before_merge = int(psql("SELECT count(*) FROM public.core_objectchange")[0][0])

    results = [{}, {}]
    shells = [threading.Thread(target=lambda r=r: r.update(nbshell(MERGE)), daemon=True) for r in results]
    for s in shells:
        s.start()
    time.sleep(20)  # both shells booted and waiting on GO_FILE
    subprocess.run(["docker", "exec", NETBOX, "touch", GO_FILE], check=True)
    for s in shells:
        s.join(timeout=1800)

    status = psql(f"SELECT status FROM netbox_branching_branch WHERE id = {branch['id']}")[0][0]
    events = psql(f"SELECT type, count(*) FROM netbox_branching_branchevent WHERE branch_id = {branch['id']} GROUP BY type")
    in_branch = object_keys(branch["schema"])
    in_main_rows = {
        "device": int(psql("SELECT count(*) FROM public.dcim_device")[0][0]),
        "interface": int(psql("SELECT count(*) FROM public.dcim_interface")[0][0]),
        "ipaddress": int(psql("SELECT count(*) FROM public.ipam_ipaddress")[0][0]),
    }
    branch_ocs = int(psql(f"SELECT count(*) FROM {branch['schema']}.core_objectchange")[0][0])
    main_oc_after = int(psql("SELECT count(*) FROM public.core_objectchange")[0][0])
    return {
        "params": {"devices": args.devices, "ifaces": args.ifaces, "kind": kind},
        "phases": phases,
        "branching": {
            "branch": branch,
            "kind": kind,
            "merges": results,
            # ObjectChanges the merges wrote into main; == branch changes when
            # merged once, a multiple of it when applied more than once.
            "main_objectchanges_from_merge": main_oc_after - main_oc_before_merge,
            "final_status": status,
            "events": {t: int(n) for t, n in events},
            "objects_in_branch": {k: len(v) for k, v in in_branch.items()},
            # rows, not distinct keys: a change applied twice shows up as > branch
            "rows_in_main": in_main_rows,
            "objectchanges_in_branch": branch_ocs,
            "applied_changes": int(psql(f"SELECT count(*) FROM netbox_branching_appliedchange WHERE branch_id = {branch['id']}")[0][0]),
            # F1: merged by one job, then put back to READY by the other's rollback
            "merged_then_ready": status == "ready" and int(dict(events).get("merged", 0)) > 0,
        },
    }


# F6: objects written in main (with change logging) so the sync has work.
# event_tracking + a fake request is how runscript gets ObjectChanges.
SYNC_OBJECTS = int(os.environ.get("SYNC_OBJECTS", "400"))
MAIN_WRITES = f"""
import json, uuid
from django.contrib.auth import get_user_model
from extras.models import Tag
from netbox.context_managers import event_tracking
from utilities.request import NetBoxFakeRequest
user = get_user_model().objects.get(username='admin')
req = NetBoxFakeRequest({{'META': {{}}, 'COOKIES': {{}}, 'POST': {{}}, 'GET': {{}}, 'FILES': {{}},
                         'user': user, 'method': 'POST', 'path': '', 'id': uuid.uuid4()}})
with event_tracking(req):
    for i in range({SYNC_OBJECTS}):
        Tag.objects.create(name=f'bench-sync-{{i}}', slug=f'bench-sync-{{i}}')
print('BENCH ' + json.dumps({{'tags': Tag.objects.filter(slug__startswith='bench-sync-').count()}}))
"""

SYNC_GO_FILE = "/tmp/bench-sync-go"
SYNC = f"""
import json, os, time
from django.contrib.auth import get_user_model
from netbox_branching.models import Branch
user = get_user_model().objects.get(username='admin')
while not os.path.exists({SYNC_GO_FILE!r}):
    time.sleep(0.02)
b = Branch.objects.get(name={BRANCH_NAME!r})
unsynced_at_go = b.get_unsynced_changes().count()
t0 = time.time(); t0_wall = time.time(); err = None
try:
    b.sync(user=user)
except Exception as e:
    err = f'{{type(e).__name__}}: {{e}}'
b.refresh_from_db()
print('BENCH ' + json.dumps({{'status': b.status, 'error': err, 'sync_s': round(time.time() - t0, 3),
                              'started_epoch': t0_wall, 'ended_epoch': time.time(),
                              'unsynced_at_go': unsynced_at_go,
                              'unsynced_after': b.get_unsynced_changes().count()}}))
"""


def branch_sync_ingest():
    """F6: Diode keeps ingesting into its default branch while the branch syncs.

    While the branch is SYNCING, netbox-branching's middleware answers every
    request that selects it with 400 "Selected branch is not ready."; the
    reconciler does not retry and marks each entity of the batch FAILED.
    Report the FAILED entities (and their error text), the sync duration, and
    how many dataset objects never reached the branch.
    """
    import argparse
    args = argparse.Namespace(devices=int(os.environ.get("DEVICES", "60")), ifaces=4, batch=50, seed=42,
                              offset=0, grpc="127.0.0.1:58081", pg_port=55432, redis_port=56379,
                              poll=0.5, timeout=1800)
    sync_at = float(os.environ.get("SYNC_AT", "0.3"))
    branch = nbshell(SETUP, timeout=600)
    main_writes = nbshell(MAIN_WRITES, timeout=600)  # after the branch: these are unsynced main changes
    subprocess.run(["docker", "restart", RECONCILER], check=True, capture_output=True)
    time.sleep(5)

    import grpc
    from netboxlabs.diode.sdk.diode.v1 import ingester_pb2_grpc
    stub = ingester_pb2_grpc.IngesterServiceStub(grpc.insecure_channel(args.grpc))
    md = [("authorization", f"Bearer {loadgen.fake_jwt()}")]
    pipe = loadgen.Pipeline(args)
    entities = loadgen.build_dataset(args.devices, args.ifaces, args.seed, "initial")

    sync = {}
    sync_result = {}
    shell = threading.Thread(target=lambda: sync_result.update(nbshell(SYNC)), daemon=True)
    shell.start()

    def syncer():
        target = int(len(entities) * sync_at)
        while sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL) < target:
            time.sleep(0.1)
        sync["started_at_settled"] = sum(pipe.states().get(k, 0) for k in loadgen.TERMINAL)
        sync["failed_before"] = pipe.states().get(4, 0)
        subprocess.run(["docker", "exec", NETBOX, "touch", SYNC_GO_FILE], check=True)
        sync["started_s"] = round(time.monotonic() - t0, 3)
        shell.join(timeout=1800)
        sync["failed_at_end_of_sync"] = pipe.states().get(4, 0)
        sync.update(sync_result)

    t0 = time.monotonic()
    th = threading.Thread(target=syncer, daemon=True)
    th.start()
    phase = loadgen.run_phase("initial", entities, args, pipe, stub, md)
    th.join(timeout=1800)

    with pipe.pg.cursor() as cur:
        cur.execute("SELECT left(coalesce(error::text, ''), 200), count(*) FROM ingestion_logs "
                    "WHERE state = 4 GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
        failed_errors = [[e, n] for e, n in cur.fetchall()]
    in_branch = object_keys(branch["schema"])
    expected = {"device": args.devices, "interface": args.devices * args.ifaces,
                "ipaddress": args.devices * args.ifaces}
    tags_in_branch = int(psql(f"SELECT count(*) FROM {branch['schema']}.extras_tag "
                              f"WHERE slug LIKE 'bench-sync-%'")[0][0])
    return {
        "params": {"devices": args.devices, "ifaces": args.ifaces, "sync_at": sync_at,
                   "sync_objects": SYNC_OBJECTS},
        "phases": [phase],
        "branching": {
            "branch": branch,
            "main_writes": main_writes,
            "sync": sync,
            "tags_synced_to_branch": tags_in_branch,
            # F6: entities whose batch hit the branch while it was not READY.
            "ingest_failed": phase.get("states_after", {}).get("FAILED", 0),
            "failed_errors": failed_errors,
            "objects_in_branch": {k: len(v) for k, v in in_branch.items()},
            "missing_in_branch": {k: expected[k] - len(in_branch[k]) for k in expected},
        },
    }


SCENARIOS = {"branch-merge-race": branch_merge_race, "branch-double-merge": branch_double_merge,
             "branch-sync-ingest": branch_sync_ingest}

if __name__ == "__main__":
    print(json.dumps(SCENARIOS[sys.argv[1]]()))
