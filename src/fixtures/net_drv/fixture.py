"""Synthetic net-drv-ts fixture provider."""

from core.synthetic_fixture import Package, RunProfile, SyntheticFixture, TestFamily


def families(names: str, objective: str) -> tuple[TestFamily, ...]:
    return tuple(
        TestFamily(name, f"{objective}: {name.replace('_', ' ')}.")
        for name in names.split()
    )


packet_variants = tuple(
    {"protocol": protocol, "payload_len": payload}
    for protocol in ("udp", "tcp")
    for payload in ("64", "512", "1500")
)

profiles = (
    RunProfile(
        name="virtio_virtio-dain-linux-mm-601",
        kind="ok",
        metas={
            "CFG": "virtio_virtio:dain",
            "TS_NAME": "net-drv-ts",
        },
        tags={
            "pci-1af4-1000": None,
            "peer-qemu-virtio-net": None,
            "qemu-virtio-net": None,
            "linux-mm": "601",
            "virtio-pci": None,
            "pci-1af4": None,
            "pci-sub-1af4": None,
            "pci-sub-1af4-0001": None,
            "port-Other": None,
            "sp-unknown": None,
            "active-port-count": "1",
            "iut-cpus": "2",
            "iut-no-ptp": None,
            "max-combined-channels": "1",
            "rx-queues": "1",
            "tst-cpus": "2",
            "tst-no-ptp": None,
        },
    ),
    RunProfile(
        name="beechbone-e810-linux-mm-608",
        kind="ok",
        metas={
            "CFG": "beechbone-e810",
            "TS_NAME": "net-drv-ts",
        },
        tags={
            "ice": None,
            "linux-mm": "608",
            "pci-8086-159b": None,
            "peer-ice": None,
            "pci-8086": None,
            "pci-sub-8086": None,
            "pci-sub-8086-0003": None,
        },
    ),
    RunProfile(
        name="dain-sfc-linux-mm-519-warning",
        kind="warning",
        metas={
            "CFG": "dain-sfc",
            "TS_NAME": "net-drv-ts",
        },
        tags={
            "linux-mm": "519",
            "pci-1924-0a03": None,
            "peer-sfc": None,
            "sfc": None,
            "kernel-linux": None,
            "pci-1924": None,
            "pci-sub-1924": None,
            "pci-sub-1924-8017": None,
        },
    ),
    RunProfile(
        name="virtio_virtio-dain-import-error",
        kind="status-error",
        metas={
            "CFG": "virtio_virtio:dain",
            "TS_NAME": "net-drv-ts",
        },
        tags={},
    ),
)

fixture = SyntheticFixture(
    name="net-drv-ts",
    project="tsf/net-drv",
    revision_meta="NET_DRV_TS",
    revision_url="https://github.com/ts-factory/net-drv-ts.git",
    tags={
        "virtio-pci": None,
        "qemu-virtio-net": None,
        "linux-mm": "synthetic",
        "max_rx_queues": "2",
        "max_tx_queues": "2",
    },
    profiles=profiles,
    packages=(
        Package(
            name="prologue",
            objective="Prepare Linux interfaces and test agents.",
            tests=(TestFamily("prologue", "Initialize the network test rig."),),
        ),
        Package(
            name="basic",
            objective="Validate fundamental Linux network driver operations.",
            tests=(
                TestFamily(
                    "rx_mode",
                    "Validate receive mode changes and packet delivery.",
                    tuple(
                        {"mode": mode, "state": state}
                        for mode in ("promisc", "allmulti")
                        for state in ("on", "off")
                    ),
                    ("IP4",),
                ),
                TestFamily(
                    "send_receive",
                    "Exchange representative TCP and UDP traffic.",
                    packet_variants,
                    ("ETHERNET",),
                ),
                *families(
                    """
                    driver_info ethtool_reset_nic mac_change_tx mac_change_rx ping
                    read_sysfs mtu_tcp mtu_udp set_if_down multicast set_rx_headroom
                    """,
                    "Validate a basic driver operation",
                ),
            ),
        ),
        Package(
            name="devlink",
            objective="Validate devlink information and resource reporting.",
            tests=families(
                "dist_layout ct_thresh separated_cpu serialno",
                "Validate devlink data",
            ),
        ),
        Package(
            name="ethtool",
            objective="Validate ethtool controls and diagnostics.",
            tests=families(
                """
                dev_properties statistics msglvl reset_under_traffic check_ring
                show_pause ts_info register_dump eeprom_dump dump_module_eeprom
                show_eee show_fec show_module eee loopback
                """,
                "Validate an ethtool operation",
            ),
        ),
        Package(
            name="offload",
            objective="Exercise network stack and NIC offloads.",
            tests=(
                TestFamily(
                    "simple_csum",
                    "Validate receive and transmit checksum offload.",
                    packet_variants,
                    ("CHECKSUM",),
                ),
                TestFamily(
                    "tso",
                    "Validate TCP segmentation offload.",
                    tuple({"mss": value} for value in ("536", "1200", "1460")),
                    ("TSO",),
                ),
                *families(
                    "receive_offload vlan_filter",
                    "Validate a network offload",
                ),
            ),
        ),
        Package(
            name="ptp",
            objective="Validate hardware clock and synchronization APIs.",
            tests=families(
                """
                get_time set_time adj_setoffset adj_frequency clock_caps sys_offset
                sys_offset_extended sys_offset_precise ptp4l
                """,
                "Validate a PTP operation",
            ),
        ),
        Package(
            name="rss",
            objective="Prepare and finalize receive-side scaling tests.",
            tests=families("prologue epilogue", "Handle RSS suite state"),
        ),
        Package(
            name="rx_path",
            objective="Validate receive-path behavior.",
            tests=(
                TestFamily(
                    "rx_fcs",
                    "Check FCS handling with valid and damaged frames.",
                    tuple({"frame": value} for value in ("valid", "bad-fcs")),
                    ("RX_FCS",),
                ),
                *families(
                    "rx_coalesce_usecs rx_coalesce_frames",
                    "Validate RX interrupt coalescing",
                ),
            ),
        ),
        Package(
            name="stress",
            objective="Exercise repeated driver and link state transitions.",
            tests=families(
                "driver_unload driver_unload_traffic if_down_up_loop",
                "Run a network-driver stress scenario",
            ),
        ),
    ),
)
