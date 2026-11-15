"""Load generator for the Diode end-to-end benchmark.

Builds entities with the official Python SDK (same protobuf a real producer
sends) and pushes them straight to the ingester's gRPC port. Then it waits
until the whole pipeline is drained and NetBox has the result.

Phases (run in order on a fresh stack):
  initial   every object is new           -> CREATE path
  reingest  identical payloads again      -> dedup + re-plan, NO_CHANGES
  update    interface description changed -> UPDATE path

"Drained" = ingest stream empty AND no ingestion log QUEUED(1)/APPLYING(8),
observed on two consecutive polls. Works for all phases because a deduplicated
entity requeues its existing row instead of inserting a new one.

Output: one JSON document on stdout (written to a file by run.sh).
"""

import argparse
import json
import random
import statistics
import time
import uuid

import grpc
import psycopg
import redis

from netboxlabs.diode.sdk.diode.v1 import ingester_pb2, ingester_pb2_grpc
from netboxlabs.diode.sdk.ingester import Device, Entity, Interface, IPAddress

TERMINAL = {3: "APPLIED", 4: "FAILED", 5: "NO_CHANGES", 6: "IGNORED", 7: "ERRORED"}
STREAM = "diode.v1.ingest-stream"


def fake_jwt():
    # The ingester only parses the JWT (no signature check) and requires the
    # diode:ingest scope; same token for both versions.
    import base64
    def b64(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'none'})}.{b64({'sub': 'bench', 'scope': 'diode:ingest', 'exp': int(time.time()) + 86400})}."


def build_dataset(devices, ifaces, seed, phase, offset=0):
    rnd = random.Random(seed)
    sites = [f"bench-site-{i:02d}" for i in range(10)]
    models = [(f"bench-mfr-{i}", f"bench-model-{i}") for i in range(5)]
    roles = ["access", "core", "edge"]
    entities = []
    for d in range(offset, offset + devices):
        mfr, model = models[rnd.randrange(len(models))]
        dev = Device(
            name=f"bench-dev-{d:05d}",
            device_type=model,
            manufacturer=mfr,
            # One platform per manufacturer: the SDK copies the device's
            # manufacturer onto the platform, so a platform shared across
            # manufacturers flips on every apply (see journal P-6).
            platform=f"{mfr}-os",
            site=sites[d % len(sites)],
            role=roles[rnd.randrange(len(roles))],
            serial=f"SN{d:08d}",
            status="active",
            tags=["bench"],
        )
        entities.append(Entity(device=dev))
        for i in range(ifaces):
            desc = f"uplink {i}" if phase != "update" else f"uplink {i} (rev2)"
            iface = Interface(device=dev, name=f"eth{i}", type="1000base-t",
                              enabled=True, mtu=1500, description=desc)
            entities.append(Entity(interface=iface))
            # unique per (device, iface); +1 skips the 10.0.0.0 network ID
            n = d * ifaces + i + 1
            addr = f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}/16"
            entities.append(Entity(ip_address=IPAddress(
                address=addr, status="active", assigned_object_interface=iface)))
    return entities


class Pipeline:
    def __init__(self, args):
        self.pg = psycopg.connect(f"host=127.0.0.1 port={args.pg_port} dbname=diode user=diode password=bench",
                                  autocommit=True)
        self.r = redis.Redis(host="127.0.0.1", port=args.redis_port, password="bench", db=1)

    def states(self):
        with self.pg.cursor() as cur:
            cur.execute("SELECT state, count(*) FROM ingestion_logs GROUP BY state")
            return {s: n for s, n in cur.fetchall()}

    def backlog(self):
        st = self.states()
        try:
            xlen = self.r.xlen(STREAM)
        except redis.ResponseError:
            xlen = 0
        return xlen, st.get(1, 0) + st.get(8, 0), st


def run_phase(name, entities, args, pipe, stub, md):
    batch = args.batch
    reqs = [entities[i:i + batch] for i in range(0, len(entities), batch)]
    before = pipe.states()
    t0 = time.monotonic()
    send_lat = []
    for chunk in reqs:
        req = ingester_pb2.IngestRequest(
            stream=STREAM, id=str(uuid.uuid4()), entities=chunk,
            sdk_name="diode-bench", sdk_version="0.0.1",
            producer_app_name="diode-bench", producer_app_version="0.0.1")
        s = time.monotonic()
        resp = stub.Ingest(req, metadata=md, timeout=30)
        send_lat.append(time.monotonic() - s)
        if resp.errors:
            raise SystemExit(f"ingest errors: {list(resp.errors)[:3]}")
    t_sent = time.monotonic() - t0

    # Progress curve: (seconds since t0, entities settled this phase)
    curve, stable, last = [], 0, None
    deadline = t0 + args.timeout
    while True:
        xlen, pending, st = pipe.backlog()
        settled = sum(st.get(k, 0) for k in TERMINAL)
        curve.append((round(time.monotonic() - t0, 3), xlen, pending, settled))
        if xlen == 0 and pending == 0 and time.monotonic() - t0 > t_sent:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        if time.monotonic() > deadline:
            # Keep the end state: a scenario can end with rows deliberately left
            # QUEUED (e.g. Diode failing closed on a merged default branch).
            return {"phase": name, "timeout": True, "entities": len(entities), "elapsed_s": args.timeout,
                    "states_before": {TERMINAL.get(k, str(k)): v for k, v in sorted(before.items())},
                    "states_after": {TERMINAL.get(k, str(k)): v for k, v in sorted(pipe.states().items())},
                    "curve": curve}
        time.sleep(args.poll)
    elapsed = time.monotonic() - t0
    after = pipe.states()
    return {
        "phase": name,
        "entities": len(entities),
        "requests": len(reqs),
        "send_s": round(t_sent, 3),
        "send_req_p50_ms": round(statistics.median(send_lat) * 1000, 2),
        "send_req_p95_ms": round(sorted(send_lat)[int(len(send_lat) * 0.95) - 1] * 1000, 2) if len(send_lat) > 1 else None,
        "elapsed_s": round(elapsed, 3),
        "entities_per_s": round(len(entities) / elapsed, 2),
        "states_before": {TERMINAL.get(k, str(k)): v for k, v in sorted(before.items())},
        "states_after": {TERMINAL.get(k, str(k)): v for k, v in sorted(after.items())},
        "curve": curve,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=200)
    ap.add_argument("--ifaces", type=int, default=4)
    ap.add_argument("--batch", type=int, default=50, help="entities per IngestRequest")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--offset", type=int, default=0, help="first device index (fresh objects on a used stack)")
    ap.add_argument("--phases", default="initial,reingest,update")
    ap.add_argument("--grpc", default="127.0.0.1:58081")
    ap.add_argument("--pg-port", type=int, default=55432)
    ap.add_argument("--redis-port", type=int, default=56379)
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()

    channel = grpc.insecure_channel(args.grpc, options=[("grpc.max_send_message_length", 64 << 20)])
    stub = ingester_pb2_grpc.IngesterServiceStub(channel)
    md = [("authorization", f"Bearer {fake_jwt()}")]
    pipe = Pipeline(args)

    out = {"params": vars(args), "phases": []}
    for phase in args.phases.split(","):
        ents = build_dataset(args.devices, args.ifaces, args.seed, phase, args.offset)
        res = run_phase(phase, ents, args, pipe, stub, md)
        out["phases"].append(res)
        if res.get("timeout"):
            break
    print(json.dumps(out))


if __name__ == "__main__":
    main()
