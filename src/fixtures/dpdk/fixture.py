"""Synthetic dpdk-ethdev-ts fixture provider.

Test objectives are authored here; the exact per-iteration ``params``/``reqs`` are
sourced from a real run via ``real_data.REAL`` (see ``tools/gen_real_data.py``).
"""

from typing import Any

from core.synthetic_fixture import (
    Package,
    RunProfile,
    SyntheticFixture,
    TestFamily,
    real_families,
    real_family,
)

from fixtures.dpdk.real_data import REAL


def families(objectives: dict[str, str]) -> tuple[TestFamily, ...]:
    """Build one TestFamily per ``name -> objective`` mapping, with real params/reqs."""
    return real_families(REAL, objectives)


# Objectives transcribed verbatim from the real dpdk-ethdev-ts run tree.
usecase_objectives = {
    "rx_one_packet_ip4": "Receive a burst of packets",
    "rx_one_packet_ip6": "Receive a burst of packets",
    "tx_multi_burst": "Transmit packet(s) using tmpl from the several TX queues",
    "promiscuous_mode": "Check correct work of promiscuous mode",
    "all_multicast_mode": "Check correct work of all-multicast mode",
    "get_mtu": "Get MTU test",
    "set_mtu": "Set MTU of IUT",
    "set_default_mac_addr": "Set the default MAC address",
    "test_detach": "Test detach",
    "dev_conf_rss_adv": "Check that RSS settings may be customised on configuration step",
    "rss": "Test RSS viability",
    "rss_hash_info": "Test checks that ethdev writes right rss hash info to the packet",
    "rss_hash_conf_get": "Get current configuration of RSS hash computation",
    "update_rss_hash_conf": "Update the RSS hash configuration",
    "rss_reta_query": "Query Redirection Table of RSS",
    "rss_reta_update": "Update the Redirection Table of RSS",
    "flow_ctrl_get": (
        "Get current status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_mode": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_high_low_water": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_zero_pause_time": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_pause_time": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_send_xon": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_autoneg": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "flow_ctrl_set_mac_ctrl_frame_fwd": (
        "Set new status of the Ethernet link flow control for Ethernet device"
    ),
    "rx_scatter": "Test checks work of RX scatter function with different buffer sizes",
    "rx_buf_size": "Test checks work of RX scatter function with different buffer sizes",
    "link_up_down": "The parntner reaction to link status changes on iut_port",
    "deferred_start_rx_queue": (
        "Deferred start of random RX queue and checking that it works properly"
    ),
    "deferred_start_tx_queue": (
        "Deferred start TX queue and checking that it works properly"
    ),
    "runtime_rx_queue_setup_with_flow": (
        "Setup Rx queue when device is started, perform the check using flow API"
    ),
    "runtime_rx_queue_setup_with_rss": (
        "Setup Rx queue when device is started, perform the check using RSS"
    ),
    "runtime_tx_queue_setup": "Setup Tx queue when device is started",
    "rx_stats": "Check the correctness of Rx statistics",
    "tx_stats": "Check the correctness of Tx statistics",
    "xstats_by_id": "Verify that xstat names and values could be retrieved by IDs",
    "xstats_dev_state": "Examine xstats values in different device state",
    "stats_reset": "Check the correctness of statistics reset",
    "rx_descriptor_status": (
        "Prove that Rx descriptor status callback readings are consistent"
    ),
    "rx_desc_nb": (
        "Given some descriptor count, verify queue setup and packet reception"
    ),
    "tx_desc_nb": (
        "Given some descriptor count, verify queue setup and packet transmit"
    ),
    "tunnel_udp_port_config": (
        "Check that tunnel UDP port could be added and deleted correctly"
    ),
    "tx_descriptor_status": (
        "Prove that Tx descriptor status callback readings are consistent"
    ),
    "rx_offload_checksum": "Make sure that valid Rx checksum flags are put into mbufs",
    "rx_ptype_ip4": (
        "Make sure that traffic classification is carried out properly by the driver"
    ),
    "rx_ptype_ip6": (
        "Make sure that traffic classification is carried out properly by the driver"
    ),
    "rx_ring_wrap": "Make sure that driver can correctly refill Rx queue desc ring",
    "set_mc_addr_list": (
        "Set the list of multicast addresses to filter on iut_port port"
    ),
    "fw_version": "Make sure that FW version could be retrieved successfully",
    "dev_reconfigure": (
        "Reconfigure the device in stopped state and check that RSS configuration "
        "are applied and all queues could transmit packets."
    ),
    "dev_info_persistence": (
        "The test gets dev_info in initialized state and then check that it remains "
        "the same in all other states"
    ),
    "rx_intr": (
        "The test requests Rx queue interrupts on device configuration then checks "
        "that Rx interrupts are triggered when enabled"
    ),
    "vlan_strip_ip4": "Check VLAN strip offload",
    "vlan_strip_ip6": "Check VLAN strip offload",
    "vlan_filter": "Check VLAN filter offload",
    "tx_pvid": "Check port based VLAN ID insertion support",
    "io_forward_and_drop": (
        "Check that IO-forwarded packets do not erroneously bypass the flow engine"
    ),
    "fec": "Verify setting FEC mode and link transitions associated with that",
}

_VLAN_CKSUM = "Check VLAN insertion and checksum offloads when one {} is sent"
_TSO = "Check TSO and VLAN insertion when one {} is sent"
_TSO_ENCAP = "Check encapsulated TSO and VLAN insertion when one {} is sent"
_EVAL_TX = "Evaluate transmit operation correctness by sending one packet"
_SEG = {
    "contig": "in one memory segment",
    "seg": "in few memory segments",
    "many_seg": "in many memory segments",
}

xmit_objectives = {
    "one_packet_tunnel": "Make sure that a packet can be sent correctly",
    **{
        f"one_packet_ip4_{seg}": _VLAN_CKSUM.format(f"IPv4 packet {desc}")
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip4_{seg}": _TSO.format(f"IPv4 packet {desc}")
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip4_encap_ip4_{seg}": _TSO_ENCAP.format(
            f"IPv4-in-IPv4 packet {desc}"
        )
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip6_encap_ip4_{seg}": _TSO_ENCAP.format(
            f"IPv6-in-IPv4 packet {desc}"
        )
        for seg, desc in _SEG.items()
    },
    **{
        f"one_packet_ip6_{seg}": _VLAN_CKSUM.format(f"IPv6 packet {desc}")
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip6_{seg}": _TSO.format(f"IPv6 packet {desc}")
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip4_encap_ip6_{seg}": _TSO_ENCAP.format(
            f"IPv4-in-IPv6 packet {desc}"
        )
        for seg, desc in _SEG.items()
    },
    **{
        f"tso_packet_ip6_encap_ip6_{seg}": _TSO_ENCAP.format(
            f"IPv6-in-IPv6 packet {desc}"
        )
        for seg, desc in _SEG.items()
    },
    "alternate_vlan": (
        "Make sure that VLAN ID alternation performed by means of sending couples of "
        "mbufs with different VLAN IDs is carried out properly"
    ),
    "vlan_on_port_restart": (
        "Make sure that PMD does not loose previously configured VLAN TCI if a port "
        "restart takes place between two packet bursts with the same VLAN TCI set in "
        "the mbufs before and after the port restart"
    ),
    "vlan_on_packet_drop": (
        "Check that VLAN offload is not broken by previous packet drop"
    ),
    "vlan_txqs_interference": (
        "Make sure that VLAN offloads on a TxQ have no impact on the others"
    ),
    "reap_on_stop": (
        "Make sure that PMD is able to free all remaining mbufs connected with any of "
        "descriptors pending on port stop"
    ),
    "one_packet_with_dpdk_rx_prologue": "",
    "one_packet_with_dpdk_rx_cksum_offloads_plain_ip4": _EVAL_TX,
    "one_packet_with_dpdk_rx_cksum_offloads_plain_ip6": _EVAL_TX,
    "one_packet_with_dpdk_rx_cksum_offloads_encap_ip4_inner_ip4": _EVAL_TX,
    "one_packet_with_dpdk_rx_cksum_offloads_encap_ip4_inner_ip6": _EVAL_TX,
    "one_packet_with_dpdk_rx_cksum_offloads_encap_ip6_inner_ip4": _EVAL_TX,
    "one_packet_with_dpdk_rx_cksum_offloads_encap_ip6_inner_ip6": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_outgoing_frames_plain": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_outgoing_frames_encap": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_header_segments_plain": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_header_segments_encap": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_payload_segments_plain": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_many_payload_segments_encap": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_long_payload_plain": _EVAL_TX,
    "tso_packet_with_dpdk_rx_too_long_payload_encap": _EVAL_TX,
    "one_packet_with_dpdk_rx_epilogue": "",
}

_FLOW_FILTER = "Verify Flow API by adding a filter and inspecting the inbound traffic"
_FLOW_DROP = "Make sure that RTE flow API DROP action is carried out correctly"
_FLOW_MARK = "Make sure that RTE flow API MARK and FLAG actions are carried out correctly"

filter_objectives = {
    "flow_rule_in2q": _FLOW_FILTER,
    "flow_rule_in2q_ip6": _FLOW_FILTER,
    "flow_rule_in2q_tunnel": _FLOW_FILTER,
    "flow_rule_rss": "Make sure that RTE flow API RSS action is carried out correctly",
    "flow_rule_drop": _FLOW_DROP,
    "flow_rule_drop_and_count_ip4": _FLOW_DROP,
    "flow_rule_drop_and_count_ip6": _FLOW_DROP,
    "flow_rule_drop_and_count_tunnel": _FLOW_DROP,
    "flow_rule_vlan": (
        "Make sure that RTE flow API VLAN ID matching is carried out correctly"
    ),
    "flow_rule_flag": _FLOW_MARK,
    "flow_rule_mark": _FLOW_MARK,
    "flow_rule_counters": (
        "Make sure that RTE flow API COUNT actions are carried out correctly"
    ),
    "flow_rule_encap_on_egress": (
        "Check that flow API encap action on egress is carried out correctly"
    ),
    "flow_rule_decap_on_ingress": (
        "Check that flow API decap action on ingress is carried out correctly"
    ),
    "flow_rule_vlan_push": (
        "Check that flow API VLAN tag push action is carried out correctly"
    ),
    "flow_rule_multi_count": "Test multiple count actions in a flow rule",
    "flow_rule_reflect": (
        "Make sure that RTE flow API action engine can reflect Rx traffic"
    ),
    "flow_rule_dec_ttl": "Check that flow API DEC_TTL action is executed correctly",
    "flow_tunnel": "Verify basic tunnel offload operability",
}

# Non-charted perf tests (objectives verbatim from the run tree).
perf_objectives = {
    "perf_prologue": "",
    "testpmd_loopback": "Test dpdk-testpmd performance in loopback mode",
    "testpmd_txonly_tso": "Test dpdk-testpmd performance in Tx only mode",
    "testpmd_txonly_tso_multiseg": "Test dpdk-testpmd performance in Tx only mode",
}

# testpmd performance charts: axis_x values shared across all perf tests (report.json).
PKT_SIZES = (42, 60, 124, 252, 508, 1020, 1514, 2044, 4092, 9014)

# Flow control argument groupings that split a test into separate report blocks.
_FC_OFF = {
    "testpmd_command_flow_ctrl_autoneg": "off",
    "testpmd_command_flow_ctrl_rx": "off",
    "testpmd_command_flow_ctrl_tx": "off",
}
_FC_ON = {
    "testpmd_command_flow_ctrl_autoneg": "on",
    "testpmd_command_flow_ctrl_rx": "on",
    "testpmd_command_flow_ctrl_tx": "on",
}

# Single source of truth for the perf report: each spec drives both the generated
# measurement leaves and the corresponding report-config test entry. Mirrors the
# Table of Contents of the real "DPDK performance" report (report.json):
#   - ``x_arg``: report axis_x (a test argument)
#   - ``overlay``: report series args (overlay_by), aligned to ``series`` tuples
#   - ``groupings``: extra varying args that split the test into arg-val blocks
#   - ``sides``: one measurement per Side value; each yields pps + throughput
PERF_CHARTS: tuple[dict[str, Any], ...] = (
    {
        "name": "testpmd_txonly",
        "objective": "Test dpdk-testpmd performance in Tx only mode",
        "tool": "testpmd",
        "x_arg": "testpmd_command_txpkts",
        "overlay": ("testpmd_arg_txq", "n_fwd_cores"),
        "series": ((1, 1), (2, 2), (4, 4), (8, 4)),
        "sides": ("Tx",),
        "groupings": (
            {"testpmd_arg_burst": "32"},
            {"testpmd_arg_burst": "64"},
        ),
    },
    {
        "name": "testpmd_rxonly",
        "objective": "Test dpdk-testpmd performance in rxonly mode",
        "tool": "testpmd",
        "x_arg": "packet_size",
        "overlay": ("testpmd_arg_rxq", "n_rx_cores"),
        "series": ((1, 1), (2, 2), (4, 4), (8, 8)),
        "sides": ("Rx",),
        "groupings": (
            {"testpmd_arg_burst": "32", **_FC_OFF},
            {"testpmd_arg_burst": "64", **_FC_OFF},
            {"testpmd_arg_burst": "32", **_FC_ON},
            {"testpmd_arg_burst": "64", **_FC_ON},
        ),
    },
    {
        "name": "testpmd_dual_port_txonly",
        "objective": (
            "Test dpdk-testpmd performance in Tx only mode on two ports simultaneously"
        ),
        "tool": "testpmd",
        "x_arg": "testpmd_command_txpkts",
        "overlay": ("testpmd_arg_txq", "n_fwd_cores"),
        "series": ((1, 2), (2, 4), (4, 4), (8, 8)),
        "sides": ("Tx",),
        "groupings": ({},),
    },
    {
        "name": "testpmd_dual_port_rxonly",
        "objective": (
            "Test dpdk-testpmd performance in Rx only mode on two ports simultaneously"
        ),
        "tool": "testpmd",
        "x_arg": "packet_size",
        "overlay": ("testpmd_arg_rxq", "n_rx_cores"),
        "series": ((1, 2), (2, 4), (4, 4), (8, 8)),
        "sides": ("Rx",),
        "groupings": (_FC_OFF, _FC_ON),
    },
    {
        "name": "testpmd_dual_port_fwd",
        "objective": (
            "Test dpdk-testpmd performance in IO forwarding mode on two ports "
            "simultaneously"
        ),
        "tool": "testpmd",
        "x_arg": "packet_size",
        "overlay": ("testpmd_arg_rxq", "n_cores"),
        "series": ((1, 2), (2, 4), (4, 4), (8, 8)),
        "sides": ("FwdRx", "FwdTx"),
        "groupings": ({},),
    },
    {
        "name": "l2fwd_simple",
        "objective": "Test l2fwd performance",
        "tool": "l2fwd",
        "x_arg": "packet_size",
        "overlay": (),
        "series": ((),),
        "sides": ("FwdTx",),
        "groupings": ({},),
    },
)


def perf_measurement(tool: str, side: str, size: int) -> dict[str, Any]:
    """Synthetic ~line-rate pps/throughput for one frame size at 10 GbE."""
    frame = max(size, 64) + 20  # on-wire bytes incl. preamble + IPG
    pps = round(10e9 / (8 * frame), 1)
    mbps = round(pps * size * 8 / 1e6, 1)

    def result(rtype: str, value: float, units: str) -> dict[str, Any]:
        return {
            "type": rtype,
            "name": rtype,
            "description": f"{side} {rtype}",
            "entries": [
                {"aggr": "mean", "value": value, "base_units": units, "multiplier": 1}
            ],
        }

    return {
        "type": "measurement",
        "version": 1,
        "tool": tool,
        "keys": {"Side": side},
        "comments": {},
        "results": [result("pps", pps, "pps"), result("throughput", mbps, "Mbps")],
        "views": [],
    }


def _size_token(value: str) -> int:
    """First integer in a perf size argument (real txpkts can be comma lists)."""
    head = value.split(",", 1)[0].strip()
    try:
        return int(head, 0)
    except ValueError:
        return 64


def perf_families(spec: dict[str, Any]) -> tuple[TestFamily, ...]:
    """One leaf per real iteration, carrying its exact params/reqs + a measurement.

    Parameters and requirements come from the reference run; the measurement
    (pps + throughput per Side) stays synthetic, derived from the iteration's
    ``x_arg`` size so the DPDK performance report still resolves on real axes.
    """
    families: list[TestFamily] = []
    for iteration in REAL.get(spec["name"], ()):
        params = dict(iteration["params"])
        size = _size_token(params.get(spec["x_arg"], "0"))
        measurements = tuple(
            perf_measurement(spec["tool"], side, size) for side in spec["sides"]
        )
        families.append(
            TestFamily(
                spec["name"],
                spec["objective"],
                parameters=(params,),
                requirements=tuple(iteration.get("reqs", ())),
                measurements=measurements,
            )
        )
    return tuple(families)


def perf_report_entry(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the report-config test entry (pps + throughput per Side) for a spec."""
    axis_y: list[dict[str, Any]] = []
    for side in spec["sides"]:
        for rtype in ("pps", "throughput"):
            axis_y.append(
                {
                    "tool": [spec["tool"]],
                    "type": [rtype],
                    "aggr": ["mean"],
                    "keys": {"Side": [side]},
                }
            )
    entry: dict[str, Any] = {
        "table_view": True,
        "chart_view": True,
        "axis_x": {"arg": spec["x_arg"], "label": spec["x_arg"]},
        "axis_y": axis_y,
        "not_show_args": {},
        "records_order": [],
    }
    if spec["overlay"]:
        entry["overlay_by"] = [{"arg": arg} for arg in spec["overlay"]]
    return entry


perf_report_config = {
    "name": "DPDK performance",
    "description": "DPDK performance report based on dpdk-ethdev-ts testing results",
    "content": {
        "title_content": ["CAMPAIGN_DATE", "CFG"],
        "test_names_order": [spec["name"] for spec in PERF_CHARTS],
        "tests": {spec["name"]: perf_report_entry(spec) for spec in PERF_CHARTS},
    },
}

profiles = (
    RunProfile(
        name="virtio_virtio-dain-linux-mm-612",
        kind="ok",
        metas={
            "CFG": "virtio_virtio:dain",
            "TS_NAME": "dpdk-ethdev-ts",
        },
        tags={
            "net_virtio": None,
            "pci-1af4-1000": None,
            "peer-qemu-virtio-net": None,
            "qemu-virtio-net": None,
            "linux-mm": "612",
            "uio_pci_generic": None,
            "kernel-linux": None,
            "max_rx_queues": "1",
            "max_tx_queues": "1",
            "pci-1af4": None,
            "dpdk": "26070001",
            "dpdk-26.07.0-rc1": None,
        },
    ),
    RunProfile(
        name="balin-x710-p0-linux-mm-515",
        kind="ok",
        metas={
            "CFG": "balin-x710-p0",
            "TS_NAME": "dpdk-ethdev-ts",
        },
        tags={
            "net_i40e": None,
            "pci-8086-1572": None,
            "linux-mm": "515",
            "vfio-pci": None,
            "kernel-linux": None,
            "num_vfs": "64",
            "pci-8086": None,
            "pci-sub-8086": None,
            "pci-sub-8086-0008": None,
            "dpdk": "25030001",
            "dpdk-25.03.0-rc1": None,
        },
    ),
    RunProfile(
        name="galdor-x710-linux-mm-608-unexpected",
        kind="result-error",
        metas={
            "CFG": "galdor-x710",
            "TS_NAME": "dpdk-ethdev-ts",
        },
        tags={
            "pci-8086-1572": None,
            "linux-mm": "608",
            "vfio-pci": None,
            "kernel-linux": None,
            "num_vfs": "64",
            "pci-8086": None,
            "pci-sub-8086": None,
            "pci-sub-8086-0008": None,
            "dpdk": "26070001",
            "dpdk-26.07.0-rc1": None,
        },
    ),
    RunProfile(
        name="virtio_virtio-dain-import-error",
        kind="status-error",
        metas={
            "CFG": "virtio_virtio:dain",
            "TS_NAME": "dpdk-ethdev-ts",
        },
        tags={},
    ),
)

fixture = SyntheticFixture(
    name="dpdk-ethdev-ts",
    project="tsf/dpdk-ethdev",
    revision_meta="DPDK_ETHDEV_TS",
    revision_url="https://github.com/ts-factory/dpdk-ethdev-ts.git",
    root_objective="DPDM EthDev Test Suite",
    report_configs=(perf_report_config,),
    tags={
        "dpdk": "synthetic",
        "vfio-pci": None,
        "pci-8086": None,
        "max_rx_queues": "2",
        "max_tx_queues": "2",
    },
    profiles=profiles,
    tests=(real_family(REAL, "prologue", "Prepare the DPDK test environment."),),
    packages=(
        Package(
            name="usecases",
            objective="Main use cases of the PMD",
            tests=(
                real_family(
                    REAL,
                    "tx_burst_simple",
                    "Transmit packets using tmpl from the TX queue",
                ),
                real_family(REAL, "rx_burst_simple", "Receive a burst of packets"),
                *families(usecase_objectives),
            ),
        ),
        Package(
            name="xmit",
            objective="Transmit Functionality",
            tests=families(xmit_objectives),
        ),
        Package(
            name="filters",
            objective="Filters",
            tests=families(filter_objectives),
        ),
        Package(
            name="representors",
            objective="Representors",
            tests=(real_family(REAL, "rep_prologue", ""),),
        ),
        Package(
            name="perf",
            objective="Performance",
            tests=(
                *families(perf_objectives),
                *(
                    family
                    for spec in PERF_CHARTS
                    for family in perf_families(spec)
                ),
            ),
        ),
    ),
)
