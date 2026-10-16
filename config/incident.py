"""Explicit lab profiles and fixed service tuples; no agency-wide port trust."""
from copy import deepcopy

# Values are protected floor / desired allocation / hard maximum, bits/sec.
PROFILES = {
    "normal": {"dispatch": (1000000, 1000000, 2000000), "medical": (1000000, 2000000, 8000000),
               "video": (1000000, 6000000, 12000000), "shelter": (500000, 1000000, 4000000), "bulk": (100000, 500000, 30000000)},
    "evacuation": {"dispatch": (1000000, 2000000, 4000000), "medical": (1000000, 4000000, 8000000),
                   "video": (500000, 2000000, 4000000), "shelter": (1000000, 4000000, 8000000), "bulk": (100000, 250000, 1000000)},
    "search-and-rescue": {"dispatch": (1000000, 2000000, 4000000), "medical": (1000000, 2000000, 4000000),
                          "video": (2000000, 12000000, 18000000), "shelter": (500000, 1000000, 2000000), "bulk": (100000, 250000, 1000000)},
    "degraded": {"dispatch": (500000, 1000000, 2000000), "medical": (500000, 1000000, 2000000),
                 "video": (0, 0, 0), "shelter": (250000, 500000, 1000000), "bulk": (0, 0, 0)},
}
PRIORITY = {"dispatch": 0, "medical": 1, "shelter": 2, "video": 3, "bulk": 4}
ESSENTIAL = {"dispatch", "medical"}
HEADROOM = 500000  # Every switch egress reserves this for health/diagnostics.


def catalog(districts=2):
    result = {}
    for prefix in "ab"[:districts]:
        for number, (kind, source, target, proto) in enumerate((
                ("dispatch", "f1", "c1", "udp"), ("medical", "h1", "c2", "tcp"),
                ("video", "f2", "c3", "udp"), ("shelter", "s1", "c2", "tcp"),
                ("bulk", "s2", "c2", "tcp"), ("medical", "f3", "c2", "tcp")), 1):
            name = prefix + ("_reporting" if number == 6 else "_" + kind)
            result[name] = {"source": prefix + source, "target": prefix + target, "protocol": proto,
                            "client_port": 19000 + number, "server_port": 9000 + number,
                            "class": kind, "queue_index": len(result) + 1}
    if districts == 2:
        result["district_coordination"] = {"source": "ac1", "target": "bc1", "protocol": "udp",
            "client_port": 19020, "server_port": 9020, "class": "dispatch", "queue_index": 20}
    return result


def initial_config(inventory):
    services = catalog(inventory.districts)
    return {"profile": "normal", "registered": sorted(set(inventory.ENDPOINTS) - {"af3", "bf3"}),
            "permissions": sorted(name for name in services if not name.endswith("_reporting")),
            "rates": {}, "constraints": {}}


def clone(config):
    return deepcopy(config)
