"""Synthetic dpdk-ethdev-ts fixture provider."""

from core.synthetic_fixture import Package, RunProfile, SyntheticFixture, TestFamily


def families(names: str, objective: str) -> tuple[TestFamily, ...]:
    return tuple(
        TestFamily(name, f"{objective}: {name.replace('_', ' ')}.")
        for name in names.split()
    )


queue_variants = tuple(
    {"queue": str(queue), "nb_pkts": packets, "payload_len": payload}
    for queue in range(2)
    for packets in ("1", "32")
    for payload in ("64", "1500")
)

usecase_names = """
rx_one_packet_ip4 rx_one_packet_ip6 tx_multi_burst promiscuous_mode
all_multicast_mode get_mtu set_mtu set_default_mac_addr test_detach
dev_conf_rss_adv rss rss_hash_info rss_hash_conf_get update_rss_hash_conf
rss_reta_query rss_reta_update flow_ctrl_get flow_ctrl_set_mode
flow_ctrl_set_high_low_water flow_ctrl_set_zero_pause_time
flow_ctrl_set_pause_time flow_ctrl_set_send_xon flow_ctrl_set_autoneg
flow_ctrl_set_mac_ctrl_frame_fwd rx_scatter rx_buf_size link_up_down
deferred_start_rx_queue deferred_start_tx_queue runtime_rx_queue_setup_with_flow
runtime_rx_queue_setup_with_rss runtime_tx_queue_setup rx_stats tx_stats
xstats_by_id xstats_dev_state stats_reset rx_descriptor_status rx_desc_nb
tx_desc_nb tunnel_udp_port_config tx_descriptor_status rx_offload_checksum
rx_ptype_ip4 rx_ptype_ip6 rx_ring_wrap set_mc_addr_list fw_version
dev_reconfigure dev_info_persistence rx_intr vlan_strip_ip4 vlan_strip_ip6
vlan_filter tx_pvid io_forward_and_drop fec
"""

xmit_names = """
one_packet_tunnel one_packet_ip4_contig one_packet_ip4_seg
one_packet_ip4_many_seg tso_packet_ip4_contig tso_packet_ip4_seg
tso_packet_ip4_many_seg tso_packet_ip4_encap_ip4_contig
tso_packet_ip4_encap_ip4_seg tso_packet_ip4_encap_ip4_many_seg
tso_packet_ip6_encap_ip4_contig tso_packet_ip6_encap_ip4_seg
tso_packet_ip6_encap_ip4_many_seg one_packet_ip6_contig one_packet_ip6_seg
one_packet_ip6_many_seg tso_packet_ip6_contig tso_packet_ip6_seg
tso_packet_ip6_many_seg tso_packet_ip4_encap_ip6_contig
tso_packet_ip4_encap_ip6_seg tso_packet_ip4_encap_ip6_many_seg
tso_packet_ip6_encap_ip6_contig tso_packet_ip6_encap_ip6_seg
tso_packet_ip6_encap_ip6_many_seg alternate_vlan vlan_on_port_restart
vlan_on_packet_drop vlan_txqs_interference reap_on_stop
one_packet_with_dpdk_rx_prologue one_packet_with_dpdk_rx_cksum_offloads_plain_ip4
one_packet_with_dpdk_rx_cksum_offloads_plain_ip6
one_packet_with_dpdk_rx_cksum_offloads_encap_ip4_inner_ip4
one_packet_with_dpdk_rx_cksum_offloads_encap_ip4_inner_ip6
one_packet_with_dpdk_rx_cksum_offloads_encap_ip6_inner_ip4
one_packet_with_dpdk_rx_cksum_offloads_encap_ip6_inner_ip6
tso_packet_with_dpdk_rx_too_many_outgoing_frames_plain
tso_packet_with_dpdk_rx_too_many_outgoing_frames_encap
tso_packet_with_dpdk_rx_too_many_header_segments_plain
tso_packet_with_dpdk_rx_too_many_header_segments_encap
tso_packet_with_dpdk_rx_too_many_payload_segments_plain
tso_packet_with_dpdk_rx_too_many_payload_segments_encap
tso_packet_with_dpdk_rx_too_long_payload_plain
tso_packet_with_dpdk_rx_too_long_payload_encap one_packet_with_dpdk_rx_epilogue
"""

filter_names = """
flow_rule_in2q flow_rule_in2q_ip6 flow_rule_in2q_tunnel flow_rule_rss
flow_rule_drop flow_rule_drop_and_count_ip4 flow_rule_drop_and_count_ip6
flow_rule_drop_and_count_tunnel flow_rule_vlan flow_rule_flag flow_rule_mark
flow_rule_counters flow_rule_encap_on_egress flow_rule_decap_on_ingress
flow_rule_vlan_push flow_rule_multi_count flow_rule_reflect flow_rule_dec_ttl
flow_tunnel
"""

perf_names = """
perf_prologue testpmd_loopback testpmd_txonly testpmd_dual_port_txonly
testpmd_txonly_tso testpmd_txonly_tso_multiseg testpmd_rxonly
testpmd_dual_port_rxonly testpmd_dual_port_fwd
"""

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
    tags={
        "dpdk": "synthetic",
        "vfio-pci": None,
        "pci-8086": None,
        "max_rx_queues": "2",
        "max_tx_queues": "2",
    },
    profiles=profiles,
    packages=(
        Package(
            name="prologue",
            objective="Prepare the DPDK test environment.",
            tests=(TestFamily("prologue", "Initialize EAL and test agents."),),
        ),
        Package(
            name="usecases",
            objective="Exercise Ethernet device API use cases.",
            tests=(
                TestFamily(
                    "tx_burst_simple",
                    "Transmit packet bursts through a configured TX queue.",
                    queue_variants,
                    ("X3-TR001",),
                ),
                TestFamily(
                    "rx_burst_simple",
                    "Receive packet bursts from a configured RX queue.",
                    queue_variants,
                    ("X3-TR002",),
                ),
                *families(usecase_names, "Validate an ethdev use case"),
            ),
        ),
        Package(
            name="xmit",
            objective="Validate packet construction, segmentation, and offloads.",
            tests=families(xmit_names, "Validate packet transmission"),
        ),
        Package(
            name="filters",
            objective="Validate rte_flow classification and actions.",
            tests=families(filter_names, "Validate a flow rule"),
        ),
        Package(
            name="representors",
            objective="Validate device representor initialization.",
            tests=(TestFamily("rep_prologue", "Prepare representor ports."),),
        ),
        Package(
            name="perf",
            objective="Provide representative packet-rate measurements.",
            tests=(
                *families(perf_names, "Measure an ethdev forwarding mode"),
                TestFamily(
                    "l2fwd_simple",
                    "Measure bidirectional L2 forwarding throughput.",
                    tuple({"frame_size": value} for value in ("64", "512", "1518")),
                    ("PERFORMANCE",),
                    measurements=(
                        {
                            "type": "measurement",
                            "version": 1,
                            "tool": "testpmd",
                            "keys": {"metric": "packet_rate"},
                            "comments": {},
                            "results": [
                                {
                                    "type": "metric",
                                    "name": "mpps",
                                    "description": "Forwarding rate",
                                    "entries": [
                                        {
                                            "aggr": "single",
                                            "value": 14.2,
                                            "base_units": "Mpps",
                                            "multiplier": 1,
                                        }
                                    ],
                                }
                            ],
                            "views": [],
                        },
                    ),
                ),
            ),
        ),
    ),
)
