from __future__ import annotations

import concurrent.futures
import datetime as dt
import ipaddress
import platform
import re
import socket
import subprocess
from typing import Any

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def get_local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def get_default_gateway() -> str | None:
    if platform.system().lower() == "windows":
        try:
            route_result = subprocess.run(
                ["route", "print", "0.0.0.0"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except OSError:
            route_result = None

        if route_result and route_result.stdout:
            for line in route_result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 4 and fields[0] == "0.0.0.0" and fields[1] == "0.0.0.0":
                    return fields[2]

    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except OSError:
        return None

    pending_gateway_line = False
    for line in result.stdout.splitlines():
        lower = line.lower()
        match = IP_RE.search(line)

        if pending_gateway_line and match:
            return match.group(0)

        if "gateway" in lower or "gateway padr" in lower:
            if match:
                return match.group(0)
            pending_gateway_line = True
            continue

        pending_gateway_line = False
    return None


def ping_host(ip: str, timeout_ms: int = 300) -> None:
    system = platform.system().lower()
    if system == "windows":
        command = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        timeout_seconds = max(1, int(round(timeout_ms / 1000)))
        command = ["ping", "-c", "1", "-W", str(timeout_seconds), ip]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def warm_arp_cache(subnet: ipaddress.IPv4Network, local_ip: str, workers: int = 80) -> None:
    targets = [str(ip) for ip in subnet.hosts() if str(ip) != local_ip]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(ping_host, targets):
            pass


def parse_arp_table() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except OSError:
        return []

    devices = []
    for line in result.stdout.splitlines():
        ip_match = IP_RE.search(line)
        mac_match = MAC_RE.search(line)
        if not ip_match or not mac_match:
            continue
        ip = ip_match.group(0)
        mac = normalize_mac(mac_match.group(0))
        devices.append({"ip": ip, "mac": mac})
    return devices


def reverse_dns(ip: str) -> str:
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except (socket.herror, OSError):
        return ""


def resolve_hostnames(devices: list[dict[str, str]], workers: int = 20) -> None:
    if not devices:
        return

    def _resolver(device: dict[str, str]) -> tuple[str, str]:
        return device["ip"], reverse_dns(device["ip"])

    resolved: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for ip, hostname in pool.map(_resolver, devices):
            resolved[ip] = hostname

    for device in devices:
        device["hostname"] = resolved.get(device["ip"], "")


def dedupe_devices(devices: list[dict[str, str]]) -> list[dict[str, str]]:
    by_mac: dict[str, dict[str, str]] = {}
    for device in devices:
        by_mac[device["mac"]] = device
    return list(by_mac.values())


def scan_network(aggressive: bool = True) -> dict[str, Any]:
    local_ip = get_local_ipv4()
    subnet = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    gateway = get_default_gateway()

    if aggressive:
        warm_arp_cache(subnet, local_ip)

    arp_devices = parse_arp_table()

    filtered = []
    for device in arp_devices:
        ip = ipaddress.ip_address(device["ip"])
        if ip not in subnet:
            continue
        filtered.append(device)

    devices = dedupe_devices(filtered)
    resolve_hostnames(devices)
    for device in devices:
        device["vendor"] = ""

    scanned_at = now_iso()
    return {
        "metadata": {
            "local_ip": local_ip,
            "subnet": str(subnet),
            "gateway": gateway or "",
            "scanned_at": scanned_at,
            "online_devices_found": len(devices),
        },
        "devices": devices,
    }
