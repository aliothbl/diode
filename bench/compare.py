"""Summarize and compare benchmark result files.

    python bench/compare.py bench/results/*.json            # table per version/scenario/phase
    python bench/compare.py --state bench/results/*.json    # also check NetBox end state equality

Exit code 1 with --state when two runs of the same scenario/params ended in
different NetBox states (the "no behaviour change" invariant).
"""

import argparse
import gzip
import hashlib
import os
import collections
import json
import statistics
import sys


def load(paths):
    docs = []
    for p in paths:
        try:
            d = json.load(open(p))
        except Exception as e:  # noqa: BLE001
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        d["_path"] = p
        docs.append(d)
    return docs


def key(d):
    knobs = ",".join(f"{k}={v}" for k, v in sorted((d.get("knobs") or {}).items()) if v)
    return (d["scenario"], knobs or "defaults")


def fmt(vals):
    if not vals:
        return "-"
    m = statistics.mean(vals)
    if len(vals) > 1:
        return f"{m:8.2f} ±{statistics.stdev(vals):6.2f} (n={len(vals)})"
    return f"{m:8.2f}          (n=1)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--state", action="store_true")
    a = ap.parse_args()
    docs = [d for d in load(a.files) if d.get("phases") and "+profile" not in d["scenario"]]

    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in docs:
        groups[key(d)][(d["version"], d["diode_rev"], d["plugin_rev"])].append(d)

    rc = 0
    for (scenario, knobs), byver in sorted(groups.items()):
        print(f"\n## scenario={scenario}  knobs={knobs}")
        phases = [p["phase"] for p in next(iter(byver.values()))[0]["phases"]]
        for ph in phases:
            print(f"  {ph}")
            base = None
            for ver in sorted(byver, key=lambda v: v[0] != "baseline"):
                runs = byver[ver]
                eps = [p["entities_per_s"] for r in runs for p in r["phases"] if p["phase"] == ph and "entities_per_s" in p]
                el = [p["elapsed_s"] for r in runs for p in r["phases"] if p["phase"] == ph and "elapsed_s" in p]
                delta = ""
                if ver[0] == "baseline" and eps:
                    base = statistics.mean(eps)
                elif base and eps:
                    delta = f"  {(statistics.mean(eps) / base - 1) * 100:+.1f}% ent/s"
                print(f"    {ver[0]:9s} {ver[1]:>13s}/{ver[2]:<13s} ent/s {fmt(eps)}   elapsed {fmt(el)}{delta}")
        if a.state:
            rc |= check_state(byver)
    sys.exit(rc)


def load_rows(doc):
    """Normalized end-state rows of a run, or None for runs without a rows dump."""
    p = doc["_path"][: -len(".json")] + ".rows.json.gz"
    if not os.path.exists(p):
        return None
    rows = json.load(gzip.open(p))
    # "path" is ignored for runs recorded before dump_state.py skipped it.
    return {m: sorted(json.dumps({k: v for k, v in json.loads(x).items() if k != "path"}, sort_keys=True)
                      for x in rs) for m, rs in rows.items()}


def check_state(byver):
    """Group runs by end state; explain differences against the largest group."""
    states = collections.defaultdict(list)
    rows_of = {}
    for ver, runs in byver.items():
        for r in runs:
            rows = load_rows(r)
            if rows is None:
                continue
            h = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]
            states[h].append(f"{ver[0]}/{ver[1]}/run{r['run']}")
            rows_of[h] = rows
    if not states:
        print("  NetBox end state: no rows dumps to compare")
        return 0
    if len(states) == 1:
        print(f"  NetBox end state: IDENTICAL across {sum(map(len, states.values()))} runs")
        return 0
    ref = max(states, key=lambda h: len(states[h]))
    print(f"  NetBox end state: {len(states)} different states; reference {ref} = {states[ref]}")
    for h, who in states.items():
        if h == ref:
            continue
        notes = []
        for m, ref_rows in rows_of[ref].items():
            rs = rows_of[h].get(m, [])
            dups = len(rs) - len(set(rs))
            if dups:
                notes.append(f"{m}: {dups} duplicate rows")
            missing, extra = set(ref_rows) - set(rs), set(rs) - set(ref_rows)
            if missing or extra:
                notes.append(f"{m}: {len(missing)} rows differ from reference")
        print(f"    {h} {who}: " + "; ".join(notes))
    return 1


if __name__ == "__main__":
    main()
