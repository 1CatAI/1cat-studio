# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import ipaddress
import socket

import psutil

_PRIVATE = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
)
_VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "cni", "podman")


def lan_api_urls(port: int) -> list[str]:
    """Return active private-network addresses, with the default route first."""
    primary = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # UDP connect selects a route without sending a packet.
            sock.connect(("192.0.2.1", 1))
            primary = sock.getsockname()[0]
    except OSError:
        pass

    stats = psutil.net_if_stats()
    addresses = set()
    for name, entries in psutil.net_if_addrs().items():
        if name.startswith(_VIRTUAL_PREFIXES) or not stats.get(name) or not stats[name].isup:
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.IPv4Address(entry.address)
            except ipaddress.AddressValueError:
                continue
            if any(address in network for network in _PRIVATE):
                addresses.add(str(address))
    return [
        f"http://{address}:{port}/v1"
        for address in sorted(addresses, key=lambda value: (value != primary, value))
    ]
