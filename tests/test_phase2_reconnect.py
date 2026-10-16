"""Real Mininet OVS cache semantics with modeled OVSDB, no network mutation."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from mininet.node import OVSSwitch
from tests import phase2_harness as harness

OLD = "11111111-1111-1111-1111-111111111111"
NEW = "22222222-2222-2222-2222-222222222222"


class SwitchLab:
    def __init__(self, connected=True):
        self.current = OLD
        self.ready = connected
        self.device = object.__new__(OVSSwitch)
        self.device.name = "p2s1"
        self.device.dpid = "%016x" % 201
        self.device.inNamespace = False
        self.device.intfs, self.device.ports = {}, {}
        self.device.failMode, self.device.inband = "secure", False
        self.device.protocols, self.device.stp = "OpenFlow13", False
        self.device.datapath = "kernel"
        self.device.listenPort, self.device.reconnectms = None, 1000
        self.device.batch = False
        self.device._uuids = [OLD]
        self.device.isOldOVS = lambda: False
        self.device.cmd = self.cmd
        self.controller = NS(name="p2c0", protocol="tcp", port=16333, IP=lambda: "127.0.0.1")

    def cmd(self, *args, **kwargs):
        text = " ".join(str(arg) for arg in args)
        if "create Controller" in text and "add-br p2s1" in text:
            self.current = NEW
            return ""
        if "get Bridge p2s1 Controller" in text:
            return "[" + self.current + "]"
        if "get Controller" in text and "is_connected" in text:
            return "true" if self.current in text and self.ready else "false"
        raise AssertionError("Unexpected Mininet command: " + text)

    def __getitem__(self, name):
        return {"p2s1": self.device, "p2c0": self.controller}[name]

    def waitConnected(self, timeout):
        return self.device.connected()

    def ovs(self, *args):
        if args[:5] == ("ovs-vsctl", "--timeout=5", "get", "Controller", NEW):
            if args[5] == "is_connected":
                return "true" if self.ready else "false"
            if args[5] == "target":
                return '"tcp:127.0.0.1:16333"'
        raise AssertionError("Unexpected OVSDB command: " + repr(args))


class ReconnectionTests(unittest.TestCase):
    def test_real_mininet_restart_retains_old_uuid_until_explicit_refresh(self):
        lab = SwitchLab()
        lab.device.start([lab.controller])
        self.assertEqual(lab.current, NEW)
        self.assertEqual(lab.device.controllerUUIDs(), [OLD])
        self.assertFalse(lab.device.connected())
        self.assertEqual(lab.device.controllerUUIDs(update=True), [NEW])
        self.assertTrue(lab.device.connected())

    def test_restart_refreshes_cache_and_saves_current_connection_evidence(self):
        lab = SwitchLab()
        with tempfile.TemporaryDirectory() as temp, patch.object(harness, "command", side_effect=lab.ovs):
            harness.restart_access_switch(lab, "p2s1", Path(temp))
            state = json.loads((Path(temp) / "p2s1_reconnection.json").read_text())
        self.assertTrue(state["mininet_connected"])
        self.assertEqual(set(state["current_controllers"]), {NEW})
        self.assertEqual(state["current_controllers"][NEW]["is_connected"], "true")

    def test_actual_reconnection_failure_still_fails_with_readback(self):
        lab = SwitchLab(connected=False)
        with tempfile.TemporaryDirectory() as temp, patch.object(harness, "command", side_effect=lab.ovs):
            with self.assertRaisesRegex(AssertionError, "did not reconnect"):
                harness.restart_access_switch(lab, "p2s1", Path(temp))
            state = json.loads((Path(temp) / "p2s1_reconnection.json").read_text())
        self.assertFalse(state["mininet_connected"])
        self.assertEqual(state["current_controllers"][NEW]["is_connected"], "false")

    def test_ovsdb_readback_cannot_be_overridden_by_optimistic_mininet_status(self):
        lab = SwitchLab(connected=False)
        lab.waitConnected = lambda timeout: True
        with tempfile.TemporaryDirectory() as temp, patch.object(harness, "command", side_effect=lab.ovs):
            with self.assertRaisesRegex(AssertionError, "did not reconnect"):
                harness.restart_access_switch(lab, "p2s1", Path(temp))


if __name__ == "__main__":
    unittest.main()
