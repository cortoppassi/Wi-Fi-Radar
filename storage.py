from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "devices.db"
ROUTER_URL_KEY = "router_url"
ROUTER_USERNAME_KEY = "router_username"
ROUTER_PASSWORD_KEY = "router_password"


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


def strip_url_credentials(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return url

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"

    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def get_db_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                mac TEXT PRIMARY KEY,
                nickname TEXT NOT NULL DEFAULT '',
                trusted INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_ip TEXT NOT NULL DEFAULT '',
                hostname TEXT NOT NULL DEFAULT '',
                vendor TEXT NOT NULL DEFAULT '',
                is_online INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()


def fetch_setting(key: str, default: str = "") -> str:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = ?
            """,
            (key,),
        ).fetchone()
    if not row:
        return default
    return str(row["value"])


def upsert_setting(key: str, value: str) -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


def fetch_router_settings(default_url: str) -> dict[str, str]:
    router_url = strip_url_credentials(fetch_setting(ROUTER_URL_KEY, default_url).strip() or default_url)
    username = fetch_setting(ROUTER_USERNAME_KEY, "").strip()
    password = fetch_setting(ROUTER_PASSWORD_KEY, "")
    return {
        "router_url": router_url,
        "username": username,
        "password": password,
    }


def update_router_settings(payload: dict[str, Any], default_url: str) -> dict[str, str]:
    current = fetch_router_settings(default_url)

    router_url = strip_url_credentials(str(payload.get("router_url", current["router_url"])).strip() or default_url)
    username = str(payload.get("username", current["username"])).strip()
    if "password" in payload:
        password = str(payload.get("password") or "")
    else:
        password = current["password"]

    upsert_setting(ROUTER_URL_KEY, router_url)
    upsert_setting(ROUTER_USERNAME_KEY, username)
    upsert_setting(ROUTER_PASSWORD_KEY, password)

    return {
        "router_url": router_url,
        "username": username,
        "password": password,
    }


def upsert_scan_devices(devices: list[dict[str, Any]], scanned_at: str) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE devices SET is_online = 0")
        conn.execute("DELETE FROM devices WHERE mac = 'FF:FF:FF:FF:FF:FF'")
        for device in devices:
            mac = normalize_mac(device["mac"])
            hostname = (device.get("hostname") or "").strip()
            vendor = (device.get("vendor") or "").strip()
            ip = (device.get("ip") or "").strip()
            is_online = 1 if bool(device.get("online", True)) else 0

            conn.execute(
                """
                INSERT INTO devices (mac, first_seen, last_seen, last_ip, hostname, vendor, is_online)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    last_ip = excluded.last_ip,
                    is_online = excluded.is_online,
                    hostname = CASE
                        WHEN excluded.hostname <> '' THEN excluded.hostname
                        ELSE devices.hostname
                    END,
                    vendor = CASE
                        WHEN excluded.vendor <> '' THEN excluded.vendor
                        ELSE devices.vendor
                    END
                """,
                (mac, scanned_at, scanned_at, ip, hostname, vendor, is_online),
            )
        conn.commit()


def fetch_devices() -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                mac,
                nickname,
                trusted,
                blocked,
                note,
                first_seen,
                last_seen,
                last_ip,
                hostname,
                vendor,
                is_online
            FROM devices
            ORDER BY is_online DESC, blocked DESC, trusted DESC, last_seen DESC
            """
        ).fetchall()

    devices = []
    for row in rows:
        devices.append(
            {
                "mac": row["mac"],
                "nickname": row["nickname"],
                "trusted": bool(row["trusted"]),
                "blocked": bool(row["blocked"]),
                "note": row["note"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "ip": row["last_ip"],
                "hostname": row["hostname"],
                "vendor": row["vendor"],
                "online": bool(row["is_online"]),
            }
        )
    return devices


def update_device(mac: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    mac = normalize_mac(mac)

    allowed_fields = {
        "nickname": str,
        "trusted": bool,
        "blocked": bool,
        "note": str,
    }
    updates: dict[str, Any] = {}
    for key, field_type in allowed_fields.items():
        if key not in payload:
            continue
        value = payload[key]
        if field_type is bool:
            updates[key] = 1 if bool(value) else 0
        else:
            updates[key] = str(value).strip()

    if not updates:
        return fetch_device(mac)

    clauses = [f"{field} = ?" for field in updates]
    params = list(updates.values()) + [mac]

    with get_db_connection() as conn:
        conn.execute(f"UPDATE devices SET {', '.join(clauses)} WHERE mac = ?", params)
        conn.commit()

    return fetch_device(mac)


def fetch_device(mac: str) -> dict[str, Any] | None:
    mac = normalize_mac(mac)
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                mac,
                nickname,
                trusted,
                blocked,
                note,
                first_seen,
                last_seen,
                last_ip,
                hostname,
                vendor,
                is_online
            FROM devices
            WHERE mac = ?
            """,
            (mac,),
        ).fetchone()

    if not row:
        return None

    return {
        "mac": row["mac"],
        "nickname": row["nickname"],
        "trusted": bool(row["trusted"]),
        "blocked": bool(row["blocked"]),
        "note": row["note"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "ip": row["last_ip"],
        "hostname": row["hostname"],
        "vendor": row["vendor"],
        "online": bool(row["is_online"]),
    }
