"""Phase 1 inventory shared by topology, controller, QoS and demonstrations.

The chain and flat /24 preserve the working foundation. Phase 2 owns the mesh.
"""
SWITCHES = {"s1": 1, "s2": 2, "s3": 3}
CORE_LINKS = (("s1", 1, "s2", 1), ("s2", 2, "s3", 1))
CORE_RATE_MBPS, ACCESS_RATE_MBPS = 100, 50
AGENCY_HOSTS = {
    "h1": {"name": "CommandCenter", "switch": "s1", "port": 2, "priority": "normal"},
    "h2": {"name": "Hospital", "switch": "s1", "port": 3, "priority": "high"},
    "h3": {"name": "Police", "switch": "s2", "port": 3, "priority": "normal"},
    "h4": {"name": "Ambulance", "switch": "s2", "port": 4, "priority": "high"},
    "h5": {"name": "RescueTeam", "switch": "s3", "port": 2, "priority": "high"},
    "h6": {"name": "Drone", "switch": "s3", "port": 3, "priority": "medium"},
    "h7": {"name": "PublicHelpline", "switch": "s1", "port": 4, "priority": "low"},
}
for _host, _meta in AGENCY_HOSTS.items():
    _n = int(_host[1:])
    _meta.update(ip="10.0.0.%d" % _n, mac="00:00:00:00:00:%02x" % _n)
TRAFFIC_PORTS = {"ambulance_data": 5001, "hospital_critical": 5002,
                 "rescue_location": 6001, "public_helpline": 8080, "drone_video": 9000}
HOST_BY_IP = {m["ip"]: m for m in AGENCY_HOSTS.values()}
HOST_BY_MAC = {m["mac"]: m for m in AGENCY_HOSTS.values()}


def switch_name(dpid):
    return next((n for n, value in SWITCHES.items() if value == dpid), None)


def egress(switch, host):
    """Port towards a registered endpoint on the configured tree."""
    if switch not in SWITCHES:
        raise ValueError("Unknown switch: %s" % switch)
    if host["switch"] == switch:
        return host["port"]
    pending, seen = [(switch, None)], set()
    while pending:
        node, first = pending.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if node == host["switch"]:
            return first
        for a, pa, b, pb in CORE_LINKS:
            if node == a:
                pending.append((b, first if first is not None else pa))
            elif node == b:
                pending.append((a, first if first is not None else pb))
    raise ValueError("No path to endpoint")


def managed_ports():
    ports = {}
    for a, pa, b, pb in CORE_LINKS:
        ports["%s-eth%d" % (a, pa)] = CORE_RATE_MBPS
        ports["%s-eth%d" % (b, pb)] = CORE_RATE_MBPS
    for m in AGENCY_HOSTS.values():
        ports["%s-eth%d" % (m["switch"], m["port"])] = ACCESS_RATE_MBPS
    return ports


def manual_rule(ambulance=False):
    host = AGENCY_HOSTS["h4" if ambulance else "h2"]
    source = AGENCY_HOSTS["h2" if ambulance else "h3"]
    match = {"eth_type": 2048, "ipv4_dst": host["ip"], "ipv4_src": source["ip"],
             "eth_src": source["mac"], "eth_dst": host["mac"],
             "in_port": egress(host["switch"], source)}
    if ambulance:
        match.update(ip_proto=6, tcp_dst=TRAFFIC_PORTS["ambulance_data"])
    return {"dpid": SWITCHES[host["switch"]], "table_id": 0,
            "cookie": 0xD150000000000002, "priority": 700,
            "match": match, "actions": [{"type": "SET_QUEUE", "queue_id": 0},
                                          {"type": "OUTPUT", "port": host["port"]}]}


def shell_bindings():
    """Fixed inventory values for legacy shell helpers (no shell code input)."""
    values = {"DPID_" + name.upper(): dpid for name, dpid in SWITCHES.items()}
    roles = {"COMMAND_CENTER": "h1", "HOSPITAL": "h2", "POLICE": "h3", "AMBULANCE": "h4",
             "RESCUE": "h5", "DRONE": "h6", "PUBLIC_HELPLINE": "h7"}
    values.update({"IP_" + role: AGENCY_HOSTS[host]["ip"] for role, host in roles.items()})
    values.update(PORT_AMBULANCE=TRAFFIC_PORTS["ambulance_data"],
                  PORT_HELPLINE=TRAFFIC_PORTS["public_helpline"],
                  QOS_SWITCH=AGENCY_HOSTS["h4"]["switch"],
                  QOS_PORT="%s-eth%d" % (AGENCY_HOSTS["h4"]["switch"], AGENCY_HOSTS["h4"]["port"]))
    import shlex
    return "\n".join(key + "=" + shlex.quote(str(value)) for key, value in values.items())


if __name__ == "__main__":
    import json
    import sys
    if sys.argv[1:] == ["shell"]:
        print(shell_bindings())
    elif sys.argv[1:] in (["manual"], ["ambulance"], ["manual-delete"]):
        rule = manual_rule(sys.argv[1] == "ambulance")
        if sys.argv[1] == "manual-delete":
            rule = {"dpid": rule["dpid"], "cookie": rule["cookie"],
                    "cookie_mask": 0xffffffffffffffff}
        print(json.dumps(rule))
    else:
        print(json.dumps({"hosts": AGENCY_HOSTS, "ports": managed_ports()}, indent=2))
