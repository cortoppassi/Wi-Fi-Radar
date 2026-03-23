from __future__ import annotations

import concurrent.futures
import datetime as dt
import ipaddress
import platform
import re
import socket
import subprocess
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
ROUTER_DEVICE_RE = re.compile(
    r'"HostName":"(?P<hostname>[^"]*)",\s*'
    r"IPAddress:'(?P<ip>[^']*)'.*?"
    r'isIpv6AddressExist:"[01]",\s*'
    r"MACAddress:'(?P<mac>[0-9a-fA-F:]{17})'.*?"
    r"AddressSource:'[^']*',\s*"
    r"Active:(?P<active>[01])",
    re.DOTALL,
)
ROUTER_DEVICE_FALLBACK_RE = re.compile(
    r"HostName:'(?P<hostname>[^']*)',\s*"
    r"MACAddress:'(?P<mac>[0-9a-fA-F:]{17})'",
    re.DOTALL,
)


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def get_router_origin(router_url: str) -> str:
    parsed = urlparse(router_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "http://192.168.1.254"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        return f"{parsed.scheme}://{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


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


def parse_router_device_records(html: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for match in ROUTER_DEVICE_RE.finditer(html):
        hostname = (match.group("hostname") or "").strip()
        mac = normalize_mac(match.group("mac"))
        ip = (match.group("ip") or "").strip()
        active = match.group("active") == "1"
        records.append(
            {
                "hostname": hostname,
                "mac": mac,
                "ip": ip,
                "active": active,
            }
        )

    if records:
        return records

    # Fallback pages may not include IP/active info.
    for match in ROUTER_DEVICE_FALLBACK_RE.finditer(html):
        hostname = (match.group("hostname") or "").strip()
        mac = normalize_mac(match.group("mac"))
        records.append(
            {
                "hostname": hostname,
                "mac": mac,
                "ip": "",
                "active": True,
            }
        )
    return records


def fetch_router_device_records(router_settings: dict[str, str] | None) -> list[dict[str, Any]]:
    if not router_settings:
        return []

    router_url = (router_settings.get("router_url") or "").strip()
    username = (router_settings.get("username") or "").strip()
    password = str(router_settings.get("password") or "")
    if not router_url or not username or not password:
        return []

    origin = get_router_origin(router_url)
    login_url = urljoin(origin + "/", "login.cgi")

    try:
        cookie_jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        login_page = opener.open(router_url, timeout=8).read().decode("utf-8", "ignore")
    except Exception:
        return []

    nonce_match = re.search(r'var\s+nonce\s*=\s*"([^"]+)"\s*;', login_page)
    token_match = re.search(r'var\s+token\s*=\s*"([^"]+)"\s*;', login_page)
    if not nonce_match or not token_match:
        return []

    payload = urlencode(
        {
            "username": username,
            "password": password,
            "csrf_token": token_match.group(1),
            "nonce": nonce_match.group(1),
        }
    ).encode()
    request = Request(
        login_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        opener.open(request, timeout=8)
    except Exception:
        return []

    for endpoint in ("lan_status.cgi?wlan", "lan_ipv4.cgi", "device_name.cgi"):
        try:
            page_html = opener.open(urljoin(origin + "/", endpoint), timeout=8).read().decode("utf-8", "ignore")
        except Exception:
            continue

        records = parse_router_device_records(page_html)
        if records:
            return records
    return []


def enrich_with_router_hostnames(devices: list[dict[str, str]], router_records: list[dict[str, Any]]) -> None:
    if not devices:
        return

    if not router_records:
        return

    by_mac: dict[str, dict[str, str]] = {}
    for record in router_records:
        mac = normalize_mac(record.get("mac", ""))
        if not mac:
            continue
        by_mac[mac] = record

    for device in devices:
        current_hostname = (device.get("hostname") or "").strip()
        if current_hostname and not current_hostname.lower().startswith("unknown_"):
            continue

        match = by_mac.get(normalize_mac(device["mac"]))
        if not match:
            continue

        router_hostname = (match.get("hostname") or "").strip()
        if router_hostname:
            device["hostname"] = router_hostname


def merge_router_devices(
    devices: list[dict[str, Any]],
    router_records: list[dict[str, Any]],
    subnet: ipaddress.IPv4Network,
) -> None:
    if not router_records:
        for device in devices:
            device["online"] = True
        return

    by_mac: dict[str, dict[str, Any]] = {}
    for device in devices:
        device["online"] = True
        by_mac[normalize_mac(device["mac"])] = device

    for record in router_records:
        mac = normalize_mac(str(record.get("mac") or ""))
        ip_value = str(record.get("ip") or "").strip()
        hostname = str(record.get("hostname") or "").strip()
        active = bool(record.get("active"))

        if not mac or not ip_value:
            continue
        try:
            ip_obj = ipaddress.ip_address(ip_value)
        except ValueError:
            continue
        if ip_obj not in subnet or mac == "FF:FF:FF:FF:FF:FF":
            continue

        existing = by_mac.get(mac)
        if existing:
            if hostname and ((not existing.get("hostname")) or str(existing.get("hostname", "")).lower().startswith("unknown_")):
                existing["hostname"] = hostname
            if active:
                existing["online"] = True
            continue

        by_mac[mac] = {
            "ip": ip_value,
            "mac": mac,
            "hostname": hostname,
            "online": active,
            "vendor": "",
        }

    devices.clear()
    devices.extend(by_mac.values())


def scan_network(aggressive: bool = True, router_settings: dict[str, str] | None = None) -> dict[str, Any]:
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
        if device["mac"] == "FF:FF:FF:FF:FF:FF":
            continue
        filtered.append(device)

    devices = dedupe_devices(filtered)
    resolve_hostnames(devices)
    router_records = fetch_router_device_records(router_settings)
    enrich_with_router_hostnames(devices, router_records)
    merge_router_devices(devices, router_records, subnet)
    for device in devices:
        device["vendor"] = ""

    scanned_at = now_iso()
    return {
        "metadata": {
            "local_ip": local_ip,
            "subnet": str(subnet),
            "gateway": gateway or "",
            "scanned_at": scanned_at,
            "online_devices_found": sum(1 for device in devices if bool(device.get("online", True))),
        },
        "devices": devices,
    }
