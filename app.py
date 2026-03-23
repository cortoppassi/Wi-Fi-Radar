from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from scanner import scan_network
from storage import fetch_device, fetch_devices, init_db, normalize_mac, update_device, upsert_scan_devices

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.1.254")

app = Flask(__name__, static_folder="static", static_url_path="/static")


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
    scan_result = scan_network(aggressive=aggressive)
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
        response["router_help"] = {
            "router_url": ROUTER_URL,
            "title": "Aplicar bloqueio no roteador",
            "steps": [
                "Abra o painel do roteador e faça login.",
                "Vá em Wi-Fi/WLAN > Controle de Acesso (Access Control).",
                f"Adicione o MAC {record['mac']} na lista de bloqueio.",
                "Salve as alterações e reinicie o Wi-Fi se necessário.",
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
    return jsonify(
        {
            "router_url": ROUTER_URL,
            "steps": [
                "Entre no painel do roteador.",
                "Abra a tela de dispositivos conectados (DHCP Clients/Associated Devices).",
                "Para bloquear, use o controle de acesso por MAC.",
            ],
        }
    )


def main() -> None:
    init_db()
    app.run(host="0.0.0.0", port=5050, debug=True)


if __name__ == "__main__":
    main()
