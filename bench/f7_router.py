"""F7 (netbox-branching): direct cost of BranchAwareRouter per ORM routing call.

    bench/.venv/bin/python bench/f7_router.py micro          # stack must be up
    bench/.venv/bin/python bench/f7_router.py spy SECONDS     # py-spy all NetBox workers
    bench/.venv/bin/python bench/f7_router.py share FILE...   # % of samples in the router

micro: in nbshell, with no active branch, times N calls of supports_branching(),
BranchAwareRouter.db_for_read() and db_for_write() for the models Diode writes,
next to two references: an empty function call, and supports_branching() behind
a per-model cache (what the "TODO: Make this more efficient" would buy).

spy: py-spy record (raw/collapsed stacks) on every granian worker for SECONDS.
share: fraction of in-request samples whose stack contains the router or
supports_branching.

Spec: aliothbl/netbox-branching SPEC-20261114-1736 (F7).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from branch_scenarios import NETBOX, nbshell  # noqa: E402

MICRO = r"""
import json, time, functools
from django.apps import apps
from netbox_branching.database import BranchAwareRouter
from netbox_branching.utilities import supports_branching
from netbox_branching.contextvars import active_branch
assert active_branch.get() is None
labels = ['dcim.Device', 'dcim.Interface', 'ipam.IPAddress', 'dcim.Site', 'dcim.DeviceType',
          'dcim.Manufacturer', 'dcim.DeviceRole', 'dcim.Platform', 'extras.Tag', 'ipam.Prefix',
          'ipam.VLAN', 'core.ObjectChange', 'extras.CustomField', 'users.User', 'contenttypes.ContentType']
models = [apps.get_model(l) for l in labels]
router = BranchAwareRouter()
N = 20000
def bench(fn):
    best = None
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(N // len(models)):
            for m in models:
                fn(m)
        dt = (time.perf_counter() - t0) / ((N // len(models)) * len(models))
        best = dt if best is None or dt < best else best
    return round(best * 1e6, 3)
cached = functools.lru_cache(maxsize=None)(supports_branching)
def noop(m):
    return None
res = {
    'noop_us': bench(noop),
    'supports_branching_us': bench(supports_branching),
    'supports_branching_cached_us': bench(cached),
    'db_for_read_us': bench(router.db_for_read),
    'db_for_write_us': bench(router.db_for_write),
    'per_model_supports_branching_us': {},
    'branching_supported': {l: supports_branching(m) for l, m in zip(labels, models)},
}
for l, m in zip(labels, models):
    t0 = time.perf_counter()
    for _ in range(5000):
        supports_branching(m)
    res['per_model_supports_branching_us'][l] = round((time.perf_counter() - t0) / 5000 * 1e6, 3)
print('BENCH ' + json.dumps(res))
"""


def micro():
    return nbshell(MICRO, timeout=600)


def worker_pids():
    out = subprocess.run(["docker", "exec", NETBOX, "sh", "-c", "ps -eo pid,args"],
                         capture_output=True, text=True, check=True).stdout
    # granian workers are the python processes besides the parent server.
    pids = [ln.split()[0] for ln in out.splitlines()[1:] if "granian" in ln]
    return pids[1:] if len(pids) > 1 else pids


def spy(seconds, outdir):
    # The NetBox image's venv has no pip: copy the static py-spy binary from
    # the bench venv (bench/.venv/bin/pip install py-spy) into the container.
    spy_bin = os.path.join(os.path.dirname(__file__), ".venv", "bin", "py-spy")
    subprocess.run(["docker", "cp", spy_bin, f"{NETBOX}:/tmp/py-spy"], check=True)
    procs = []
    for pid in worker_pids():
        procs.append(subprocess.Popen(
            ["docker", "exec", "-u", "root", NETBOX,
             "/tmp/py-spy", "record", "-r", "100", "-d", str(seconds), "-f", "raw",
             "-o", f"/tmp/spy-{pid}.txt", "-p", pid, "--nonblocking"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait()
    files = []
    for pid in worker_pids():
        dst = os.path.join(outdir, f"spy-{pid}.txt")
        if subprocess.run(["docker", "cp", f"{NETBOX}:/tmp/spy-{pid}.txt", dst],
                          capture_output=True).returncode == 0:
            files.append(dst)
    return files


def share(files):
    total = in_req = router = sb = 0
    for f in files:
        for ln in open(f):
            stack, _, n = ln.rstrip().rpartition(" ")
            n = int(n)
            total += n
            if "netbox_diode_plugin" not in stack:
                continue
            in_req += n
            if "netbox_branching/database.py" in stack:
                router += n
            if "supports_branching" in stack:
                sb += n
    pct = (lambda x: round(100.0 * x / in_req, 2) if in_req else None)
    return {"samples": total, "in_plugin_request": in_req,
            "router_samples": router, "router_pct": pct(router),
            "supports_branching_samples": sb, "supports_branching_pct": pct(sb)}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "micro":
        print(json.dumps(micro()))
    elif cmd == "spy":
        outdir = sys.argv[3] if len(sys.argv) > 3 else "."
        os.makedirs(outdir, exist_ok=True)
        print(json.dumps(share(spy(int(sys.argv[2]), outdir))))
    elif cmd == "share":
        print(json.dumps(share(sys.argv[2:])))
