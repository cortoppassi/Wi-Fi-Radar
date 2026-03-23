from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse
from urllib.request import urlopen

from flask import Flask, jsonify, request, send_from_directory

from scanner import scan_network
from storage import (
    fetch_device,
    fetch_devices,
    fetch_router_settings,
    init_db,
    normalize_mac,
    update_device,
    update_router_settings,
    upsert_scan_devices,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.1.254")
ROUTER_USERNAME = os.environ.get("ROUTER_USERNAME", "")
ROUTER_PASSWORD = os.environ.get("ROUTER_PASSWORD", "")

app = Flask(__name__, static_folder="static", static_url_path="/static")


def build_router_open_url(router_url: str, username: str, password: str) -> str:
    if not username or not password:
        return router_url

    parsed = urlparse(router_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return router_url

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"

    user_info = f"{quote(username, safe='')}:{quote(password, safe='')}"
    netloc = f"{user_info}@{host}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def get_router_config() -> dict[str, str]:
    settings = fetch_router_settings(default_url=ROUTER_URL)
    if not settings["username"] and ROUTER_USERNAME:
        settings["username"] = ROUTER_USERNAME
    if not settings["password"] and ROUTER_PASSWORD:
        settings["password"] = ROUTER_PASSWORD
    return settings


def serialize_router_config(settings: dict[str, str]) -> dict[str, Any]:
    open_url = build_router_open_url(settings["router_url"], settings["username"], settings["password"])
    return {
        "router_url": settings["router_url"],
        "username": settings["username"],
        "password": settings["password"],
        "open_url": open_url,
        "uses_embedded_auth_url": bool(settings["username"] and settings["password"]),
    }


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


def build_router_auto_login_payload(settings: dict[str, str]) -> dict[str, Any]:
    open_url = build_router_open_url(settings["router_url"], settings["username"], settings["password"])
    if not settings["username"] or not settings["password"]:
        return {"mode": "open_url", "open_url": open_url}

    login_page_url = settings["router_url"]
    router_origin = get_router_origin(settings["router_url"])
    login_url = urljoin(router_origin + "/", "login.cgi")

    try:
        with urlopen(login_page_url, timeout=8) as response:
            html = response.read().decode("utf-8", "ignore")
    except Exception:
        return {"mode": "open_url", "open_url": open_url}

    nonce_match = re.search(r'var\s+nonce\s*=\s*"([^"]+)"\s*;', html)
    token_match = re.search(r'var\s+token\s*=\s*"([^"]+)"\s*;', html)
    if not nonce_match or not token_match:
        return {"mode": "open_url", "open_url": open_url}

    return {
        "mode": "form_post",
        "open_url": open_url,
        "login_url": login_url,
        "fields": {
            "username": settings["username"],
            "password": settings["password"],
            "csrf_token": token_match.group(1),
            "nonce": nonce_match.group(1),
        },
    }


@app.get("/")
def index() -> Any:
    return send_from_directory("static", "index.html")


@app.get("/api/devices")
def get_devices() -> Any:
    devices = fetch_devices()
    totals = {
        "total": len(devices),
        "online": sum(1 for d in devices if d["online"]),
        "blocked": sum(1 for d in devices if d["blocked"]),
        "trusted": sum(1 for d in devices if d["trusted"]),
    }
    return jsonify({"devices": devices, "totals": totals})


@app.post("/api/scan")
def scan_devices() -> Any:
    payload = request.get_json(silent=True) or {}
    aggressive = bool(payload.get("aggressive", True))
    scan_result = scan_network(aggressive=aggressive, router_settings=get_router_config())
    upsert_scan_devices(scan_result["devices"], scan_result["metadata"]["scanned_at"])
    devices = fetch_devices()
    totals = {
        "total": len(devices),
        "online": sum(1 for d in devices if d["online"]),
        "blocked": sum(1 for d in devices if d["blocked"]),
        "trusted": sum(1 for d in devices if d["trusted"]),
    }
    return jsonify({"scan": scan_result["metadata"], "devices": devices, "totals": totals})


@app.patch("/api/devices/<mac>")
def patch_device(mac: str) -> Any:
    payload = request.get_json(silent=True) or {}
    record = update_device(normalize_mac(mac), payload)
    if not record:
        return jsonify({"error": "Device not found"}), 404

    response: dict[str, Any] = {"device": record}
    if record["blocked"]:
        router_settings = get_router_config()
        response["router_help"] = {
            "router_url": router_settings["router_url"],
            "open_url": build_router_open_url(
                router_settings["router_url"],
                router_settings["username"],
                router_settings["password"],
            ),
            "title": "Aplicar bloqueio no roteador",
            "steps": [
                "Abra o painel do roteador e faca login.",
                "Va em Wi-Fi/WLAN > Controle de Acesso (Access Control).",
                f"Adicione o MAC {record['mac']} na lista de bloqueio.",
                "Salve as alteracoes e reinicie o Wi-Fi se necessario.",
            ],
        }
    return jsonify(response)


@app.get("/api/devices/<mac>")
def get_device(mac: str) -> Any:
    record = fetch_device(normalize_mac(mac))
    if not record:
        return jsonify({"error": "Device not found"}), 404
    return jsonify({"device": record})


@app.get("/api/router-help")
def router_help() -> Any:
    router_settings = get_router_config()
    return jsonify(
        {
            "router_url": router_settings["router_url"],
            "open_url": build_router_open_url(
                router_settings["router_url"],
                router_settings["username"],
                router_settings["password"],
            ),
            "steps": [
                "Entre no painel do roteador.",
                "Abra a tela de dispositivos conectados (DHCP Clients/Associated Devices).",
                "Para bloquear, use o controle de acesso por MAC.",
            ],
        }
    )


@app.get("/api/router-settings")
def get_router_settings_api() -> Any:
    settings = get_router_config()
    return jsonify(serialize_router_config(settings))


@app.put("/api/router-settings")
def put_router_settings_api() -> Any:
    payload = request.get_json(silent=True) or {}
    settings = update_router_settings(payload, default_url=ROUTER_URL)
    return jsonify(serialize_router_config(settings))


@app.post("/api/router-auto-login")
def post_router_auto_login() -> Any:
    settings = get_router_config()
    return jsonify(build_router_auto_login_payload(settings))


def main() -> None:
    init_db()
    if ROUTER_USERNAME or ROUTER_PASSWORD:
        current_settings = fetch_router_settings(default_url=ROUTER_URL)
        patch_payload: dict[str, str] = {}
        if ROUTER_USERNAME and not current_settings["username"]:
            patch_payload["username"] = ROUTER_USERNAME
        if ROUTER_PASSWORD and not current_settings["password"]:
            patch_payload["password"] = ROUTER_PASSWORD
        if patch_payload:
            update_router_settings(patch_payload, default_url=ROUTER_URL)
    app.run(host="0.0.0.0", port=5050, debug=True)


if __name__ == "__main__":
    main()
