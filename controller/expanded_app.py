"""Twenty-switch Phase 2 app; reuse validated gateway/attachment semantics.

Install only changed owned rules to avoid whole-network churn at this size.
Updates are deliberate and downstream-first, not an atomic multi-switch commit.
"""
import json
import os
from controller.phase2_app import Phase2App, OWNER_MASK, ROUTE_COOKIE
from controller.matches import forwarding_match


class ExpandedPhase2App(Phase2App):
    def __init__(self, *args, **kwargs):
        kwargs.update(inventory_name="expanded", districts=int(os.environ.get("SDN_PHASE2_DISTRICTS", "2")))
        self.installed, self.collecting, self.route_cache = {}, None, {}
        self.route_entries = None
        super().__init__(*args, **kwargs)

    def delete(self, dp, cookie, mask=0xffffffffffffffff):
        if self.collecting is None:
            if mask == OWNER_MASK:
                self.installed.pop(dp.id, None)
                # A features event can reuse a Datapath object while the switch
                # table was cleared. Never trust compiled routes across it.
                self.route_cache.clear()
            super().delete(dp, cookie, mask)

    def flow(self, dp, cookie, priority, match, actions):
        if self.collecting is None:
            return super().flow(dp, cookie, priority, match, actions)
        key = (cookie, priority, tuple(sorted(match.items())))
        fingerprint = json.dumps([a.to_jsondict() for a in actions], sort_keys=True)
        self.collecting.setdefault(dp.id, {})[key] = (fingerprint, match, actions)
        self.order.append((dp.id, key))
        if self.route_entries is not None:
            self.route_entries.append((dp.id, key, self.collecting[dp.id][key]))

    def install_route(self, source, target, route, cookie=ROUTE_COOKIE, sport=None, dport=None):
        if self.collecting is None:
            return super().install_route(source, target, route, cookie, sport, dport)
        group = (source, target, cookie, sport, dport)
        # Compare complete routes, including source/target attachment ports and
        # current connection identity. Link capacities do not change actions.
        signature = (route, tuple(id(self.datapaths[self.inventory.SWITCHES[s[0]]]) for s in route))
        cached = self.route_cache.get(group)
        if cached is not None and cached[0] == signature:
            for dpid, key, value in cached[1]:
                self.collecting.setdefault(dpid, {})[key] = value
                self.order.append((dpid, key))
            return
        self.route_entries = []
        try:
            super().install_route(source, target, route, cookie, sport, dport)
            self.route_cache[group] = (signature, self.route_entries)
        finally:
            self.route_entries = None

    def reconcile(self):
        self.collecting = {}
        self.order = []
        try:
            super().reconcile()
            desired = self.collecting
        finally:
            self.collecting = None
        # Retain the global downstream-first order, not a grouping by switch.
        for dpid, key in self.order:
            fingerprint, match, actions = desired[dpid][key]
            previous = self.installed.get(dpid, {})
            if key not in previous or previous[key][0] != fingerprint:
                super().flow(self.datapaths[dpid], key[0], key[1], match, actions)
        for dpid, dp in self.datapaths.items():
            previous, current = self.installed.get(dpid, {}), desired.get(dpid, {})
            for key, (_fingerprint, match, _actions) in previous.items():
                if key not in current:
                    dp.send_msg(dp.ofproto_parser.OFPFlowMod(
                        dp, command=dp.ofproto.OFPFC_DELETE_STRICT, cookie=key[0], cookie_mask=0xffffffffffffffff,
                        priority=key[1], table_id=0, out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
                        match=forwarding_match(dp.ofproto_parser, **match)))
            self.installed[dpid] = current
