"""Refresh the expanded guide's exact inventory tables from shared configuration."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.expanded import build_inventory


def render(inventory):
    rows = ["", "### Switches", "", "| Switch | DPID (decimal) | Role |",
            "|---|---:|---|"]
    rows += ["| %s | %d | %s |" % (name, dpid, inventory.SWITCH_ROLES[name])
             for name, dpid in inventory.SWITCHES.items()]
    rows += ["", "### Subnets and virtual gateways", "",
             "| Logical site | Subnet | Gateway | Gateway MAC |", "|---|---|---|---|"]
    rows += ["| %s | %s | %s | %s |" % (name, site.subnet, site.gateway, site.gateway_mac)
             for name, site in inventory.SITES.items()]
    rows += ["", "### Hosts and attachment ports", "",
             "| Host | Logical site | IP | MAC | Host interface → switch port |",
             "|---|---|---|---|---|"]
    for name, endpoint in inventory.ENDPOINTS.items():
        attachments = "; ".join("%s-eth%d → %s:%d" % (name, a.host_port, a.switch, a.port)
                                for a in endpoint.attachments)
        rows.append("| %s | %s | %s | %s | %s |" %
                    (name, endpoint.site, endpoint.ip, endpoint.mac, attachments))
    rows += ["", "### Infrastructure links", "", "| Switch A:port | Switch B:port | Mbps |",
             "|---|---|---:|"]
    rows += ["| %s:%d | %s:%d | %d |" % (link.a, link.pa, link.b, link.pb, link.rate_mbps)
             for link in inventory.LINKS]
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    target = ROOT / "docs/13_expanded_topology.md"
    marker = "<!-- generated-inventory -->"
    content = target.read_text()
    if content.count(marker) != 1:
        raise SystemExit("Expected one inventory marker; guide left unchanged")
    target.write_text(content.split(marker)[0] + marker + "\n" + render(build_inventory()))
    print(target.relative_to(ROOT))
