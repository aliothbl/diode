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


class Inventory:
    """Device/interface/IP inventory for the `sustained` phase.

    Differs from build_dataset() in one point: the interface an IP address
    points to carries no description. Updates change interface descriptions,
    and a repeated IP must not carry a stale copy of its interface. Each
    entity is built once and reused, so a repeat is byte-identical to the
    last version sent (same entity hash in the reconciler).
    """

    def __init__(self, devices, ifaces, seed):
        self.ifaces = ifaces
        self.rnd = random.Random(seed)
        self.sites = [f"bench-site-{i:02d}" for i in range(10)]
        self.models = [(f"bench-mfr-{i}", f"bench-model-{i}") for i in range(5)]
        self.roles = ["access", "core", "edge"]
        self.base = []        # every entity of the base inventory, current version
        self.iface_slot = {}  # (device index, iface index) -> index in self.base
        self.next_device = 0
        self.rev = 0
        self.final_desc = {}  # "bench-dev-00001/eth3" -> last description sent
        self.created = {}     # entity kind -> create events handed out
        for _ in range(devices):
            self.base.extend(self._bundle(track=True))

    def _device(self, d):
        mfr, model = self.models[self.rnd.randrange(len(self.models))]
        return Device(name=f"bench-dev-{d:05d}", device_type=model, manufacturer=mfr,
                      platform=f"{mfr}-os", site=self.sites[d % len(self.sites)],
                      role=self.roles[self.rnd.randrange(len(self.roles))],
                      serial=f"SN{d:08d}", status="active", tags=["bench"])

    def _iface(self, dev, i, desc):
        return Interface(device=dev, name=f"eth{i}", type="1000base-t", enabled=True, mtu=1500,
                         description=desc)

    def _bundle(self, track):
        """Entities of one new device: the device, then (interface, IP) per port."""
        d = self.next_device
        self.next_device += 1
        dev = self._device(d)
        out = [Entity(device=dev)]
        for i in range(self.ifaces):
            if track:
                self.iface_slot[(d, i)] = len(self.base) + len(out)
            out.append(Entity(interface=self._iface(dev, i, f"uplink {i}")))
            ref = Interface(device=dev, name=f"eth{i}", type="1000base-t", enabled=True, mtu=1500)
            n = d * self.ifaces + i + 1
            addr = f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}/16"
            out.append(Entity(ip_address=IPAddress(address=addr, status="active",
                                                   assigned_object_interface=ref)))
        return out

    def creates(self):
        """Endless stream of create events: whole new devices, entity by entity.
        Counts what was actually handed out (the last bundle may be partial)."""
        while True:
            for ent in self._bundle(track=False):
                kind = ent.WhichOneof("entity")
                self.created[kind] = self.created.get(kind, 0) + 1
                yield ent

    def expected_counts(self):
        base_devices = len(self.iface_slot) // self.ifaces if self.ifaces else 0
        return {"device": base_devices + self.created.get("device", 0),
                "interface": base_devices * self.ifaces + self.created.get("interface", 0),
                "ip_address": base_devices * self.ifaces + self.created.get("ip_address", 0)}

    def update(self):
        """New description on a random base interface; it becomes that slot's current version."""
        if not hasattr(self, "_slots"):
            self._slots = list(self.iface_slot.items())
        (d, i), slot = self._slots[self.rnd.randrange(len(self._slots))]
        self.rev += 1
        desc = f"uplink {i} (rev{self.rev})"
        dev = self.base[slot].interface.device
        ent = Entity(interface=self._iface(dev, i, desc))
        self.base[slot] = ent
        self.final_desc[f"bench-dev-{d:05d}/eth{i}"] = desc
        return ent

    def repeat(self):
        return self.base[self.rnd.randrange(len(self.base))]


def send(stub, md, entities):
    rid = str(uuid.uuid4())
    req = ingester_pb2.IngestRequest(
        stream=STREAM, id=rid, entities=entities,
        sdk_name="diode-bench", sdk_version="0.0.1",
        producer_app_name="diode-bench", producer_app_version="0.0.1")
    resp = stub.Ingest(req, metadata=md, timeout=30)
    if resp.errors:
        raise SystemExit(f"ingest errors: {list(resp.errors)[:3]}")
    return rid


def pctl(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 3)


def run_sustained(inv, args, pipe, stub, md):
    """Send at a fixed rate and mix for args.duration seconds, then drain.

    Every tick (args.tick s) sends what is due for each kind: creates and
    updates in their own requests (one per kind per tick, so their ingestion
    logs can be found by request_id), repeats in requests of args.batch.

    Backlog alone cannot show whether the pipeline keeps up: a repeat whose
    row is still QUEUED only bumps duplicate_count, so the backlog never
    exceeds the number of distinct entities. The signal is the latency of
    create and update requests: send -> every row of the request terminal.
    """
    weights = [float(x) for x in args.mix.split(":")]
    kinds = ["repeat", "update", "create"]
    share = {k: w / sum(weights) for k, w in zip(kinds, weights)}
    per_s = args.rate / 60.0
    sent = {k: 0 for k in kinds}
    creates = inv.creates()
    tracked = {}   # request_id -> {"kind", "n", "t_sent"}
    latency = {"create": [], "update": []}   # (t_sent since t0, seconds)
    failed_rows = {"create": 0, "update": 0}
    samples = []
    before = pipe.states()
    t0 = time.monotonic()
    late_ticks = 0

    def observe(now):
        if tracked:
            with pipe.pg.cursor() as cur:
                cur.execute(
                    "SELECT request_id, count(*) FILTER (WHERE state IN (3,4,5,6,7)),"
                    " count(*) FILTER (WHERE state IN (4,7)) FROM ingestion_logs"
                    " WHERE request_id = ANY(%s) GROUP BY 1", (list(tracked),))
                for rid, done, bad in cur.fetchall():
                    t = tracked[rid]
                    if done >= t["n"]:
                        latency[t["kind"]].append((round(t["t_sent"], 1), round(now - t0 - t["t_sent"], 3)))
                        failed_rows[t["kind"]] += bad
                        del tracked[rid]
        xlen, pending, st = pipe.backlog()
        samples.append((round(now - t0, 1), sum(sent.values()), xlen, pending, len(tracked)))
        return xlen, pending

    tick = 0
    while True:
        tick += 1
        target_t = tick * args.tick
        sending = target_t <= args.duration + 1e-9
        delay = t0 + target_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif sending and delay < -args.tick:
            late_ticks += 1   # the generator itself fell behind by more than a tick
        if sending:
            for k in kinds:
                due = int(per_s * share[k] * target_t) - sent[k]
                if due <= 0:
                    continue
                if k == "repeat":
                    ents = [inv.repeat() for _ in range(due)]
                    for j in range(0, due, args.batch):
                        send(stub, md, ents[j:j + args.batch])
                else:
                    ents = [next(creates) for _ in range(due)] if k == "create" else [inv.update() for _ in range(due)]
                    rid = send(stub, md, ents)
                    tracked[rid] = {"kind": k, "n": due, "t_sent": time.monotonic() - t0}
                sent[k] += due
        now = time.monotonic()
        xlen, pending = observe(now)
        if not sending and xlen == 0 and pending == 0 and not tracked:
            break
        if now - t0 > args.duration + args.timeout:
            break

    end = time.monotonic() - t0
    window = [s for s in samples if s[0] <= args.duration]
    last5 = [s for s in window if s[0] >= args.duration - 300]
    backlog = lambda s: s[2] + s[3]
    slope = None
    if len(last5) >= 2:
        xs = [s[0] for s in last5]; ys = [backlog(s) for s in last5]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        slope = round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 3) if den else 0.0
    sent_window = window[-1][1] if window else 0

    def lat_summary(vals):
        half = args.duration / 2
        first = [v for t, v in vals if t < half]
        second = [v for t, v in vals if t >= half]
        all_v = [v for _, v in vals]
        return {"requests": len(vals), "p50_s": pctl(all_v, 50), "p95_s": pctl(all_v, 95), "max_s": pctl(all_v, 100),
                "p95_first_half_s": pctl(first, 95), "p95_second_half_s": pctl(second, 95)}

    return {
        "phase": "sustained",
        "rate_per_min": args.rate, "mix": args.mix, "duration_s": args.duration, "tick_s": args.tick,
        "entities": sum(sent.values()),
        "sent": sent,
        "sent_rate_per_min": round(sent_window / args.duration * 60, 1) if args.duration else None,
        "late_ticks": late_ticks,
        "elapsed_s": round(end, 3),
        "entities_per_s": round(sum(sent.values()) / args.duration, 2) if args.duration else None,
        "drain_s": round(end - args.duration, 1),
        "timeout": bool(tracked) or (samples and backlog(samples[-1]) > 0),
        "unfinished_requests": len(tracked),
        "backlog_max_window": max((backlog(s) for s in window), default=0),
        "backlog_max_last5min": max((backlog(s) for s in last5), default=0),
        "backlog_slope_last5min_per_s": slope,
        "latency": {k: lat_summary(v) for k, v in latency.items()},
        "failed_rows": failed_rows,
        "states_before": {TERMINAL.get(k, str(k)): v for k, v in sorted(before.items())},
        "states_after": {TERMINAL.get(k, str(k)): v for k, v in sorted(pipe.states().items())},
        "expected": {"counts": inv.expected_counts(), "iface_desc": inv.final_desc},
        # (t, sent so far, stream len, QUEUED+APPLYING, create/update requests in flight)
        "curve": samples,
        "latency_samples": latency,
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
    # sustained phase (phases "initial,sustained"): initial loads the base inventory
    ap.add_argument("--rate", type=float, default=17000, help="events per minute")
    ap.add_argument("--mix", default="90:7:3", help="repeat:update:create")
    ap.add_argument("--duration", type=float, default=600, help="seconds of load")
    ap.add_argument("--tick", type=float, default=1.0, help="send interval in seconds")
    args = ap.parse_args()

    channel = grpc.insecure_channel(args.grpc, options=[("grpc.max_send_message_length", 64 << 20)])
    stub = ingester_pb2_grpc.IngesterServiceStub(channel)
    md = [("authorization", f"Bearer {fake_jwt()}")]
    pipe = Pipeline(args)

    phases = args.phases.split(",")
    inv = Inventory(args.devices, args.ifaces, args.seed) if "sustained" in phases else None
    out = {"params": vars(args), "phases": []}
    for phase in phases:
        if phase == "sustained":
            res = run_sustained(inv, args, pipe, stub, md)
            out["phases"].append(res)
            continue
        ents = inv.base if (inv and phase == "initial") else \
            build_dataset(args.devices, args.ifaces, args.seed, phase, args.offset)
        res = run_phase(phase, ents, args, pipe, stub, md)
        out["phases"].append(res)
        if res.get("timeout"):
            break
    print(json.dumps(out))


if __name__ == "__main__":
    main()
