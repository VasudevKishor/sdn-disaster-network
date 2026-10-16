"""Check prerequisite order in serialized matches, not just Ryu field values."""
import struct
import unittest
try:
    from ryu.ofproto import ofproto_v1_3_parser as parser
except ImportError as exc:
    raise unittest.SkipTest("Use the pinned Ryu Python: " + str(exc))
from controller.matches import forwarding_match


def wire_fields(match):
    data = bytearray()
    match.serialize(data, 0)
    length = struct.unpack_from("!H", data, 2)[0]
    offset, result = 4, []
    while offset < length:
        oxm_class, field_mask, size = struct.unpack_from("!HBB", data, offset)
        result.append((oxm_class, field_mask >> 1))
        offset += 4 + size
    return result


class MatchTests(unittest.TestCase):
    def test_fragment_drop_and_route_matches_put_ethertype_before_fragment(self):
        for fragment in ((1, 1), 0):
            fields = dict(eth_type=0x0800, ip_frag=fragment)
            if fragment == 0:
                fields.update(in_port=1, ipv4_src="10.20.1.10", ipv4_dst="10.20.2.10",
                              ip_proto=6, tcp_dst=8080)
            match = forwarding_match(parser, **fields)
            self.assertEqual(dict(match.items()), fields)
            wire = wire_fields(match)
            self.assertLess(wire.index((0x8000, 5)), wire.index((0x0001, 26)))
            if fragment == 0:
                self.assertLess(wire.index((0x8000, 10)), wire.index((0x8000, 14)))

    def test_standard_matches_keep_ryu_normalization(self):
        for fields in ({}, {"in_port": 1}, {"eth_type": 0x0800, "ipv4_src": ("10.20.1.10", "255.255.255.0")}):
            normal, ordered = parser.OFPMatch(**fields), forwarding_match(parser, **fields)
            self.assertEqual(list(normal.items()), list(ordered.items()))
            self.assertEqual(wire_fields(normal), wire_fields(ordered))


if __name__ == "__main__":
    unittest.main()
