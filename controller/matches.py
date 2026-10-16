"""OpenFlow matches with Nicira fields ordered after their prerequisites."""


def forwarding_match(parser, **fields):
    # Ryu sorts by OXM class/field number, putting NXM_NX_IP_FRAG before
    # OXM_OF_ETH_TYPE. OVS validates prerequisites in wire order and rejects
    # that match with OFPBMC_BAD_PREREQ. Keep Ryu's normalization and ordering
    # for standard fields, then append the fragment extension after EtherType.
    ordered = list(parser.OFPMatch(**fields).items())
    if "ip_frag" in fields:
        ordered = [(name, value) for name, value in ordered if name != "ip_frag"] + [
            (name, value) for name, value in ordered if name == "ip_frag"]
    return parser.OFPMatch(_ordered_fields=ordered)
