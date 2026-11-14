"""Dump a version-independent fingerprint of the NetBox objects the benchmark touches.

Run inside the NetBox container:  manage.py nbshell < /opt/bench/dump_state.py
Prints one JSON line: {model: {"count": n, "sha256": digest}}.

Primary keys and timestamps differ between runs, so they are excluded and
foreign keys are rendered through str(related_object). Two runs that end in
the same NetBox state yield identical output regardless of apply order.
"""

import hashlib
import json

from django.apps import apps

MODELS = [
    "dcim.Site", "dcim.Manufacturer", "dcim.DeviceType", "dcim.DeviceRole",
    "dcim.Platform", "dcim.Device", "dcim.Interface",
    "ipam.IPAddress", "ipam.Prefix", "ipam.VLAN", "extras.Tag",
]
# "path" is the PK-based tree path of nested models (roles, platforms) in 4.7.
SKIP = {"id", "created", "last_updated", "_path", "_name", "lft", "rght", "tree_id", "path"}


def label(rel):
    """Identify a related object without its PK. Components (interfaces...)
    are only unique per device, so the device is part of the label."""
    if rel is None:
        return None
    parent = getattr(rel, "device", None) or getattr(rel, "virtual_machine", None)
    base = f"{rel._meta.label}:{rel}"
    return f"{base}@{parent}" if parent is not None else base


def row(obj):
    out = {}
    # GenericForeignKey id columns (assigned_object_id, scope_id...) hold raw
    # PKs whose values depend on creation order; render the target instead.
    gfks = {f.fk_field: f.name for f in obj._meta.private_fields if hasattr(f, "fk_field")}
    for f in obj._meta.concrete_fields:
        if f.name in SKIP:
            continue
        if f.attname in gfks:
            out[gfks[f.attname]] = label(getattr(obj, gfks[f.attname], None))
        elif f.is_relation:
            out[f.name] = label(getattr(obj, f.name, None))
        else:
            v = getattr(obj, f.attname)
            out[f.name] = v if isinstance(v, (int, float, bool, type(None))) else str(v)
    if hasattr(obj, "tags"):
        out["tags"] = sorted(t.name for t in obj.tags.all())
    return json.dumps(out, sort_keys=True, default=str)


result, all_rows = {}, {}
for model_label in MODELS:
    model = apps.get_model(model_label)
    rows = sorted(row(o) for o in model.objects.all())
    h = hashlib.sha256()
    for r in rows:
        h.update(r.encode())
        h.update(b"\n")
    result[model_label] = {"count": len(rows), "sha256": h.hexdigest()}
    all_rows[model_label] = rows

print("BENCH_ROWS " + json.dumps(all_rows, sort_keys=True))
print("BENCH_STATE " + json.dumps(result, sort_keys=True))
