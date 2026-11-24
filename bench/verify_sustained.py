"""Check NetBox's end state after a `sustained` run against what loadgen sent.

    verify_sustained.py <result.json> <result.rows.json.gz>

Reads the `expected` block of the sustained phase (object counts and the last
description sent to every updated interface) and the rows dump written by
run.sh (netbox/dump_state.py). Prints one JSON object and adds it to the
result as "sustained_check". Exit code 1 when anything differs.
"""

import collections
import gzip
import json
import sys


def main():
    res_path, rows_path = sys.argv[1:3]
    doc = json.load(open(res_path))
    phase = next((p for p in doc.get("phases", []) if p.get("phase") == "sustained"), None)
    if phase is None:
        print(json.dumps({"skipped": "no sustained phase"}))
        return 0
    exp = phase["expected"]
    rows = json.load(gzip.open(rows_path))

    def bench_rows(model, pred):
        return [json.loads(r) for r in rows.get(model, []) if pred(json.loads(r))]

    devices = bench_rows("dcim.Device", lambda r: str(r.get("name", "")).startswith("bench-dev-"))
    ifaces = bench_rows("dcim.Interface", lambda r: "bench-dev-" in str(r.get("device", "")))
    ips = bench_rows("ipam.IPAddress", lambda r: str(r.get("address", "")).startswith("10."))

    dup_ips = [a for a, n in collections.Counter(r["address"] for r in ips).items() if n > 1]
    desc = {f'{r["device"].split(":", 1)[1]}/{r["name"]}': r.get("description") for r in ifaces}
    wrong_desc = {k: {"expected": v, "netbox": desc.get(k)} for k, v in exp["iface_desc"].items() if desc.get(k) != v}

    got = {"device": len(devices), "interface": len(ifaces), "ip_address": len(ips)}
    check = {
        "counts_expected": exp["counts"],
        "counts_netbox": got,
        "duplicate_ip_addresses": len(dup_ips),
        "updated_interfaces": len(exp["iface_desc"]),
        "wrong_descriptions": len(wrong_desc),
        "wrong_description_examples": dict(list(wrong_desc.items())[:10]),
    }
    check["ok"] = got == exp["counts"] and not dup_ips and not wrong_desc
    doc["sustained_check"] = check
    json.dump(doc, open(res_path, "w"), indent=1)
    print(json.dumps(check))
    return 0 if check["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
