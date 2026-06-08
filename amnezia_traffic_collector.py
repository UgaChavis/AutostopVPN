#!/usr/bin/env python3
import csv
import html
import json
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
    from zoneinfo import ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


CONTAINER = os.environ.get("AMNEZIA_CONTAINER", "amnezia-awg2")
INTERFACE = os.environ.get("AMNEZIA_INTERFACE", "awg0")
DATA_DIR = Path(os.environ.get("AMNEZIA_TRAFFIC_DIR", "/var/lib/amnezia-traffic"))
TIMEZONE = os.environ.get("AMNEZIA_TRAFFIC_TZ", "Asia/Krasnoyarsk")
PING_TARGET = os.environ.get("AMNEZIA_PING_TARGET", "1.1.1.1")
ACTIVE_WINDOW_SECONDS = int(os.environ.get("AMNEZIA_ACTIVE_WINDOW_SECONDS", "180"))
PING_COUNT = int(os.environ.get("AMNEZIA_PING_COUNT", "3"))
MTU_PROBE_ENABLED = os.environ.get("AMNEZIA_MTU_PROBE", "1") != "0"
MTU_PROBE_TARGET = os.environ.get("AMNEZIA_MTU_TARGET", PING_TARGET)
MTU_PROBE_PAYLOADS = (1472, 1464, 1452, 1432, 1412, 1380)
MTU_PROBE_CACHE_SECONDS = int(os.environ.get("AMNEZIA_MTU_PROBE_CACHE_SECONDS", "3600"))

STATE_FILE = DATA_DIR / "state.json"
TOTALS_FILE = DATA_DIR / "totals.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
DAILY_DIR = DATA_DIR / "daily"
REPORTS_DIR = DATA_DIR / "reports"
WEB_DIR = DATA_DIR / "web"
ALIASES_FILE = DATA_DIR / "aliases.csv"
SERVER_INFO_FILE = DATA_DIR / "server_info.json"
WEB_SUMMARY_FILE = WEB_DIR / "dashboard.json"
WEB_INDEX_FILE = WEB_DIR / "index.html"
GEO_CACHE_FILE = DATA_DIR / "geo_cache.json"
TRANSPORT_CACHE_FILE = DATA_DIR / "transport_probe.json"
GEOLOOKUP_TIMEOUT_SECONDS = 2.0


def current_tzinfo():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TIMEZONE)
    except ZoneInfoNotFoundError:
        return None


def run_command(args: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def now_local() -> datetime:
    tzinfo = current_tzinfo()
    if tzinfo is None:
        return datetime.now(timezone.utc)
    return datetime.now(tzinfo)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def load_geo_cache() -> Dict[str, object]:
    payload = load_json(GEO_CACHE_FILE, {"hosts": {}})
    if not isinstance(payload, dict):
        return {"hosts": {}}
    hosts = payload.get("hosts", {})
    if not isinstance(hosts, dict):
        payload["hosts"] = {}
    return payload


def load_transport_cache() -> Dict[str, object]:
    payload = load_json(TRANSPORT_CACHE_FILE, {"updated_at": None, "result": {}})
    if not isinstance(payload, dict):
        return {"updated_at": None, "result": {}}
    result = payload.get("result", {})
    if not isinstance(result, dict):
        payload["result"] = {}
    return payload


def split_endpoint_host(endpoint: object) -> str:
    value = str(endpoint or "").strip()
    if not value:
        return ""
    if value.startswith("[") and "]:" in value:
        return value[1 : value.index("]")]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def lookup_public_ip_location(host: str) -> str:
    providers = (
        f"https://ipapi.co/{host}/json/",
        f"https://ip-api.com/json/{host}?fields=status,message,city,regionName,country,countryCode,org,query",
    )
    for url in providers:
        try:
            request = Request(url, headers={"User-Agent": "AutostopVPN/1.0"})
            with urlopen(request, timeout=GEOLOOKUP_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue
        if payload.get("status") == "fail" or payload.get("error"):
            continue

        city = str(payload.get("city", "")).strip()
        region = str(payload.get("region", payload.get("regionName", ""))).strip()
        country = str(payload.get("country_name", payload.get("country", ""))).strip()
        country_code = str(payload.get("country_code", payload.get("countryCode", ""))).strip()
        org = str(payload.get("org", "")).strip()
        if city or region:
            parts = [part for part in (city, region) if part]
            if country_code:
                parts.append(country_code)
            elif country:
                parts.append(country)
            return ", ".join(parts)
        if country:
            return country_code or country
        if org:
            return org

    return host


def format_peer_location(endpoint: object, geo_cache: Dict[str, object]) -> str:
    host = split_endpoint_host(endpoint)
    if not host:
        return "нет endpoint"

    hosts = geo_cache.setdefault("hosts", {})
    if not isinstance(hosts, dict):
        hosts = {}
        geo_cache["hosts"] = hosts

    cached = hosts.get(host)
    if isinstance(cached, dict):
        label = str(cached.get("label", "")).strip()
        if label:
            return label

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        label = host
    else:
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved:
            label = "локальная сеть"
        else:
            label = lookup_public_ip_location(host)

    hosts[host] = {
        "label": label,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    geo_cache["_dirty"] = True
    return label


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    WEB_DIR.mkdir(parents=True, exist_ok=True)


def load_os_release() -> Dict[str, str]:
    values: Dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def infer_provider_name(hostname: str) -> str:
    lowered = hostname.lower()
    if "mnogoweb" in lowered:
        return "Mnogoweb"
    return "Не указано"


def default_server_info() -> Dict[str, object]:
    os_release = load_os_release()
    fqdn = socket.getfqdn()
    return {
        "title": "Данные сервера",
        "server_role": "VPN и мониторинг",
        "provider_name": infer_provider_name(fqdn),
        "provider_site": "https://mnogoweb.in" if "mnogoweb" in fqdn.lower() else "",
        "purchase_note": (
            f"По тех. хостнейму сервер относится к провайдеру {infer_provider_name(fqdn)}."
            if infer_provider_name(fqdn) != "Не указано"
            else "Провайдер не определён автоматически."
        ),
        "payment_note": "Оплата через панель провайдера. Если знаете точную ссылку на биллинг, добавьте её в server_info.json.",
        "billing_url": "",
        "billing_login_hint": "",
        "public_ip": "",
        "hostname": fqdn,
        "domain": "",
        "ssh_user": "root",
        "ssh_port": 22,
        "os": os_release.get("PRETTY_NAME", ""),
        "project_path": "/opt/autostopcrm",
        "vpn_container": CONTAINER,
        "notes": [
            "Панель мониторинга доступна только через localhost и SSH-туннель.",
            "Из этого интерфейса VPN не обновляется и не перезапускается.",
        ],
    }


def load_server_info() -> Dict[str, object]:
    defaults = default_server_info()
    payload = load_json(SERVER_INFO_FILE, {})
    if not isinstance(payload, dict):
        payload = {}
    result = defaults.copy()
    result.update(payload)
    notes = payload.get("notes")
    if isinstance(notes, list):
        result["notes"] = [str(item) for item in notes if str(item).strip()]
    return result


def coerce_positive_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def detect_default_interface() -> Optional[str]:
    try:
        completed = run_command(["ip", "route", "show", "default"], check=False)
    except FileNotFoundError:
        return None
    match = re.search(r"\bdev\s+(\S+)", completed.stdout or "")
    if match:
        return match.group(1)
    return None


def read_interface_speed_mbps(interface: Optional[str]) -> Optional[float]:
    if not interface:
        return None
    speed_path = Path("/sys/class/net") / interface / "speed"
    try:
        raw_speed = speed_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        speed = float(raw_speed)
    except ValueError:
        return None
    return speed if speed > 0 else None


def get_bandwidth_capacity(server_info: Dict[str, object]) -> Dict[str, object]:
    limit_mbps = coerce_positive_float(server_info.get("bandwidth_limit_mbps"))
    limit_bytes_per_sec = coerce_positive_float(server_info.get("bandwidth_limit_bytes_per_sec"))
    source = ""
    interface = str(server_info.get("network_interface", "")).strip()

    if limit_mbps is not None:
        limit_bytes_per_sec = int(limit_mbps * 125000)
        source = "server_info.json:bandwidth_limit_mbps"
    elif limit_bytes_per_sec is not None:
        limit_bytes_per_sec = int(limit_bytes_per_sec)
        source = "server_info.json:bandwidth_limit_bytes_per_sec"
    else:
        if not interface:
            interface = detect_default_interface() or ""
        detected_mbps = read_interface_speed_mbps(interface)
        if detected_mbps is not None:
            limit_bytes_per_sec = int(detected_mbps * 125000)
            source = f"/sys/class/net/{interface}/speed" if interface else "/sys/class/net/*/speed"

    return {
        "capacity_bytes_per_sec": int(limit_bytes_per_sec or 0),
        "capacity_mbps": round((float(limit_bytes_per_sec or 0) * 8) / 1000000, 2) if limit_bytes_per_sec else 0.0,
        "source": source,
        "interface": interface,
    }


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    tzinfo = current_tzinfo()
    if parsed.tzinfo is None and tzinfo is not None:
        return parsed.replace(tzinfo=tzinfo)
    return parsed


def inspect_container() -> Dict[str, str]:
    raw = run_command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}\t{{.State.StartedAt}}\t{{.State.Status}}\t{{.Config.Image}}",
            CONTAINER,
        ]
    ).stdout.strip()
    container_id, started_at, status, image = raw.split("\t", 3)
    return {
        "container_id": container_id,
        "started_at": started_at,
        "status": status,
        "image": image,
    }


def get_wg_dump() -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    raw = run_command(["docker", "exec", CONTAINER, "wg", "show", INTERFACE, "dump"]).stdout.strip()
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"No wg dump returned for {CONTAINER}:{INTERFACE}")

    header = lines[0].split("\t")
    interface_meta = {
        "listen_port": int(header[2]) if len(header) >= 3 and header[2].isdigit() else 0,
    }

    peers: List[Dict[str, object]] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        allowed_ips = parts[3]
        vpn_ip = allowed_ips.split(",")[0].split("/")[0]
        peers.append(
            {
                "public_key": parts[0],
                "endpoint": parts[2],
                "allowed_ips": allowed_ips,
                "vpn_ip": vpn_ip,
                "latest_handshake": int(parts[4]),
                "rx_bytes": int(parts[5]),
                "tx_bytes": int(parts[6]),
            }
        )
    return interface_meta, peers


def load_aliases() -> Dict[str, Dict[str, str]]:
    aliases: Dict[str, Dict[str, str]] = {}
    if not ALIASES_FILE.exists():
        return aliases
    with ALIASES_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            public_key = (row.get("public_key") or "").strip()
            if not public_key:
                continue
            aliases[public_key] = {
                "name": (row.get("name") or "").strip(),
                "vpn_ip": (row.get("vpn_ip") or "").strip(),
            }
    return aliases


def merge_aliases(peers: List[Dict[str, object]]) -> Dict[str, Dict[str, str]]:
    aliases = load_aliases()
    changed = False
    for peer in sorted(peers, key=lambda item: str(item["vpn_ip"])):
        public_key = str(peer["public_key"])
        vpn_ip = str(peer["vpn_ip"])
        current = aliases.get(public_key)
        if current is None:
            aliases[public_key] = {"name": "", "vpn_ip": vpn_ip}
            changed = True
        elif current.get("vpn_ip") != vpn_ip:
            current["vpn_ip"] = vpn_ip
            changed = True
    if changed or not ALIASES_FILE.exists():
        with ALIASES_FILE.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["name", "vpn_ip", "public_key"])
            for public_key, data in sorted(aliases.items(), key=lambda item: item[1]["vpn_ip"]):
                writer.writerow([data.get("name", ""), data.get("vpn_ip", ""), public_key])
    return aliases


def get_ping_metrics() -> Dict[str, object]:
    completed = run_command(
        ["ping", "-c", str(PING_COUNT), "-W", "2", PING_TARGET],
        check=False,
    )
    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", combined)
    rtt_match = re.search(
        r"=\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
        combined,
    )
    return {
        "target": PING_TARGET,
        "ok": completed.returncode == 0,
        "packet_loss_percent": float(loss_match.group(1)) if loss_match else None,
        "latency_min_ms": float(rtt_match.group(1)) if rtt_match else None,
        "latency_avg_ms": float(rtt_match.group(2)) if rtt_match else None,
        "latency_max_ms": float(rtt_match.group(3)) if rtt_match else None,
        "latency_mdev_ms": float(rtt_match.group(4)) if rtt_match else None,
    }


def get_cached_transport_probe(current_time: datetime) -> Optional[Dict[str, object]]:
    cached = load_transport_cache()
    cached_result = cached.get("result", {})
    cached_at = parse_iso_datetime(cached.get("updated_at"))
    if not isinstance(cached_result, dict) or cached_at is None:
        return None

    cached_matches_config = (
        cached_result.get("target") == MTU_PROBE_TARGET
        and bool(cached_result.get("enabled")) == MTU_PROBE_ENABLED
    )
    age_seconds = max((current_time - cached_at).total_seconds(), 0.0)
    if cached_matches_config and age_seconds < MTU_PROBE_CACHE_SECONDS:
        return cached_result
    return None


def save_transport_probe_result(result: Dict[str, object], current_time: datetime) -> None:
    save_json(TRANSPORT_CACHE_FILE, {"updated_at": current_time.isoformat(), "result": result})


def get_path_mtu_probe() -> Dict[str, object]:
    current_time = now_local()
    cached_result = get_cached_transport_probe(current_time)
    if cached_result is not None:
        return cached_result

    if not MTU_PROBE_ENABLED:
        result = {
            "target": MTU_PROBE_TARGET,
            "enabled": False,
            "ok": None,
            "max_payload_bytes": None,
            "estimated_path_mtu": None,
            "tested_payloads": [],
        }
        save_transport_probe_result(result, current_time)
        return result

    ip_overhead = 48 if ":" in MTU_PROBE_TARGET else 28
    tested_payloads: List[int] = []
    for payload in MTU_PROBE_PAYLOADS:
        tested_payloads.append(payload)
        completed = run_command(
            ["ping", "-c", "1", "-W", "1", "-M", "do", "-s", str(payload), MTU_PROBE_TARGET],
            check=False,
        )
        if completed.returncode == 0:
            result = {
                "target": MTU_PROBE_TARGET,
                "enabled": True,
                "ok": True,
                "max_payload_bytes": payload,
                "estimated_path_mtu": payload + ip_overhead,
                "tested_payloads": tested_payloads,
            }
            save_transport_probe_result(result, current_time)
            return result

    result = {
        "target": MTU_PROBE_TARGET,
        "enabled": True,
        "ok": False,
        "max_payload_bytes": None,
        "estimated_path_mtu": None,
        "tested_payloads": tested_payloads,
    }
    save_transport_probe_result(result, current_time)
    return result


def get_interface_mtu() -> Optional[int]:
    completed = run_command(
        ["docker", "exec", CONTAINER, "cat", f"/sys/class/net/{INTERFACE}/mtu"],
        check=False,
    )
    raw = (completed.stdout or "").strip()
    try:
        mtu = int(raw)
    except ValueError:
        return None
    return mtu if mtu > 0 else None


def read_meminfo() -> Dict[str, int]:
    values: Dict[str, int] = {}
    with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
        for line in handle:
            key, raw_value = line.split(":", 1)
            parts = raw_value.strip().split()
            if not parts:
                continue
            values[key] = int(parts[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(total - available, 0)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": round((used / total) * 100, 2) if total else 0.0,
    }


def get_server_status(
    current_time: datetime,
    server_info: Optional[Dict[str, object]] = None,
    current_total_bps: int = 0,
) -> Dict[str, object]:
    server_info = server_info or load_server_info()
    disk_total, disk_used, disk_free = shutil.disk_usage("/")
    ping = get_ping_metrics()
    transport = get_path_mtu_probe()
    interface_mtu = get_interface_mtu()
    server_mtu_hint = server_info.get("wireguard_mtu")
    try:
        server_mtu_hint_int = int(server_mtu_hint)
    except (TypeError, ValueError):
        server_mtu_hint_int = None
    if interface_mtu is not None:
        transport["interface_mtu"] = interface_mtu
        if server_mtu_hint_int is not None and server_mtu_hint_int > 0:
            recommended_mtu = server_mtu_hint_int
        elif transport.get("estimated_path_mtu") is not None:
            recommended_mtu = max(1280, int(transport["estimated_path_mtu"]) - 80)
        else:
            recommended_mtu = 1420
        transport["recommended_interface_mtu"] = recommended_mtu
        transport["mtu_gap"] = max(interface_mtu - recommended_mtu, 0)
    else:
        transport["interface_mtu"] = None
        transport["recommended_interface_mtu"] = 1420
        transport["mtu_gap"] = None
    uptime_seconds = 0.0
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        uptime_seconds = 0.0

    load_1, load_5, load_15 = os.getloadavg()
    bandwidth = get_bandwidth_capacity(server_info)
    capacity_bytes_per_sec = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    utilization_percent = (
        round((current_total_bps / capacity_bytes_per_sec) * 100, 2) if capacity_bytes_per_sec else None
    )
    headroom_bytes_per_sec = max(capacity_bytes_per_sec - current_total_bps, 0) if capacity_bytes_per_sec else 0
    over_capacity_bytes_per_sec = max(current_total_bps - capacity_bytes_per_sec, 0) if capacity_bytes_per_sec else 0
    bandwidth.update(
        {
            "current_bytes_per_sec": int(current_total_bps),
            "utilization_percent": utilization_percent,
            "headroom_bytes_per_sec": headroom_bytes_per_sec,
            "over_capacity_bytes_per_sec": over_capacity_bytes_per_sec,
        }
    )
    return {
        "checked_at": current_time.isoformat(),
        "loadavg": {"1m": round(load_1, 2), "5m": round(load_5, 2), "15m": round(load_15, 2)},
        "memory": read_meminfo(),
        "disk_root": {
            "total_bytes": disk_total,
            "used_bytes": disk_used,
            "free_bytes": disk_free,
            "used_percent": round((disk_used / disk_total) * 100, 2) if disk_total else 0.0,
        },
        "uptime_seconds": int(uptime_seconds),
        "ping": ping,
        "transport": transport,
        "bandwidth": bandwidth,
    }


def update_daily_bandwidth_stats(
    daily: Dict[str, object],
    current_time: datetime,
    current_total_bps: int,
    bandwidth: Dict[str, object],
) -> Dict[str, object]:
    daily_bandwidth = daily.setdefault(
        "bandwidth",
        {
            "sample_count": 0,
            "sum_current_total_bps": 0.0,
            "average_current_total_bps": 0.0,
            "peak_current_total_bps": 0,
            "peak_utilization_percent": None,
            "peak_at": None,
            "last_current_total_bps": 0,
            "last_utilization_percent": None,
            "last_headroom_bytes_per_sec": 0,
            "updated_at": None,
        },
    )

    previous_peak_bps = int(daily_bandwidth.get("peak_current_total_bps", 0) or 0)
    sample_count = int(daily_bandwidth.get("sample_count", 0) or 0) + 1
    sum_current_total_bps = float(daily_bandwidth.get("sum_current_total_bps", 0.0) or 0.0) + float(current_total_bps)
    current_utilization_percent = bandwidth.get("utilization_percent")
    peak_utilization_percent = daily_bandwidth.get("peak_utilization_percent")
    if current_utilization_percent is not None:
        peak_utilization_percent = max(float(peak_utilization_percent or 0.0), float(current_utilization_percent))

    if current_total_bps >= previous_peak_bps:
        peak_at = current_time.isoformat()
        peak_current_total_bps = int(current_total_bps)
    else:
        peak_at = daily_bandwidth.get("peak_at")
        peak_current_total_bps = previous_peak_bps

    daily_bandwidth.update(
        {
            "sample_count": sample_count,
            "sum_current_total_bps": sum_current_total_bps,
            "average_current_total_bps": round(sum_current_total_bps / sample_count, 2) if sample_count else 0.0,
            "peak_current_total_bps": peak_current_total_bps,
            "peak_utilization_percent": peak_utilization_percent,
            "peak_at": peak_at,
            "last_current_total_bps": int(current_total_bps),
            "last_utilization_percent": current_utilization_percent,
            "last_headroom_bytes_per_sec": int(bandwidth.get("headroom_bytes_per_sec", 0) or 0),
            "updated_at": current_time.isoformat(),
        }
    )
    return daily_bandwidth


def describe_bandwidth_state(
    capacity_bytes_per_sec: int,
    utilization_percent: Optional[float],
    over_capacity_bytes_per_sec: int,
) -> Dict[str, object]:
    if capacity_bytes_per_sec <= 0:
        return {
            "class": "muted",
            "label": "лимит не задан",
            "note": "Укажите bandwidth_limit_mbps в amnezia_server_info.json, чтобы увидеть процент загрузки.",
            "bar_width_percent": 0.0,
        }

    utilization = float(utilization_percent or 0.0)
    bar_width_percent = min(max(utilization, 0.0), 100.0)

    if over_capacity_bytes_per_sec > 0 or utilization >= 95.0:
        return {
            "class": "danger",
            "label": "перегрузка",
            "note": "Текущий поток уже упирается в лимит канала и скорость у пользователей будет падать.",
            "bar_width_percent": bar_width_percent,
        }

    if utilization >= 85.0:
        return {
            "class": "warn",
            "label": "канал близок к пределу",
            "note": "В пиковые часы может начаться просадка скорости. Следите за пиком и headroom.",
            "bar_width_percent": bar_width_percent,
        }

    if utilization >= 60.0:
        return {
            "class": "warn",
            "label": "нагрузка растет",
            "note": "Канал еще не забит, но запас уже уменьшается.",
            "bar_width_percent": bar_width_percent,
        }

    return {
        "class": "ok",
        "label": "норма",
        "note": "Запас канала достаточный.",
        "bar_width_percent": bar_width_percent,
    }


def format_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.2f} {unit}"


def format_rate(value: float) -> str:
    return f"{format_bytes(int(round(value)))}/s"


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "н/д"
    return f"{value:.2f}%"


def short_key(public_key: str) -> str:
    if len(public_key) <= 12:
        return public_key
    return f"{public_key[:6]}...{public_key[-6:]}"


def ip_sort_key(value: str) -> int:
    parts = value.split(".")
    if len(parts) != 4:
        return 0
    score = 0
    for part in parts:
        try:
            octet = int(part)
        except ValueError:
            return 0
        if octet < 0 or octet > 255:
            return 0
        score = (score << 8) + octet
    return score


def format_handshake(value: int) -> str:
    if not value:
        return "никогда"
    try:
        return datetime.fromtimestamp(value, tz=current_tzinfo()).isoformat(sep=" ")
    except (OSError, OverflowError, ValueError):
        return "ошибка"


def handshake_age_seconds(now_ts: int, handshake_ts: int) -> Optional[int]:
    if not handshake_ts:
        return None
    return max(now_ts - handshake_ts, 0)


def format_age(seconds: Optional[int]) -> str:
    if seconds is None:
        return "никогда"
    if seconds < 60:
        return f"{seconds}с"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}м {remainder}с"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}ч {minutes}м"
    days, hours = divmod(hours, 24)
    return f"{days}д {hours}ч"


def translate_container_status(value: str) -> str:
    mapping = {
        "running": "работает",
        "created": "создан",
        "exited": "остановлен",
        "dead": "ошибка",
        "paused": "пауза",
        "restarting": "перезапуск",
    }
    return mapping.get(value, value)


def format_timestamp(value: Optional[str]) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "н/д"
    return parsed.isoformat(sep=" ")


def build_warnings(summary: Dict[str, object]) -> List[str]:
    warnings: List[str] = []
    vpn = summary["vpn"]
    server = summary["server"]
    ping = server["ping"]
    transport = server.get("transport", {})
    disk = server["disk_root"]
    memory = server["memory"]
    container = summary["container"]
    bandwidth = server.get("bandwidth", {})

    if container["status"] != "running":
        warnings.append(f"VPN-контейнер сейчас в состоянии: {container['status']}.")
    if disk["used_percent"] >= 85:
        warnings.append(f"Диск `/` заполнен на {disk['used_percent']:.2f}%.")
    if memory["available_bytes"] < 256 * 1024 * 1024:
        warnings.append(f"Мало свободной памяти: {format_bytes(memory['available_bytes'])}.")
    if ping["packet_loss_percent"] is not None and ping["packet_loss_percent"] > 0:
        warnings.append(f"Потери до {ping['target']}: {ping['packet_loss_percent']:.2f}%.")
    if ping["latency_avg_ms"] is not None and ping["latency_avg_ms"] > 100:
        warnings.append(f"Средняя задержка до {ping['target']}: {ping['latency_avg_ms']:.2f} мс.")
    if isinstance(transport, dict):
        estimated_path_mtu = transport.get("estimated_path_mtu")
        if estimated_path_mtu is not None and int(estimated_path_mtu) < 1420:
            warnings.append(
                f"Похоже на MTU/fragmentation issue: path MTU около {int(estimated_path_mtu)} bytes."
            )
        interface_mtu = transport.get("interface_mtu")
        recommended_mtu = transport.get("recommended_interface_mtu")
        if interface_mtu is not None and recommended_mtu is not None and int(interface_mtu) > int(recommended_mtu):
            warnings.append(
                f"MTU awg0={int(interface_mtu)} выше ориентира {int(recommended_mtu)}; для мобильного Telegram можно попробовать 1280-1360."
            )
        elif transport.get("ok") is False:
            warnings.append(f"Не удалось подтвердить PMTU до {transport.get('target', MTU_PROBE_TARGET)}.")
    if vpn["active_connections"] == 0:
        warnings.append("Нет активных handshake в текущем окне активности.")
    bandwidth_utilization = bandwidth.get("utilization_percent")
    if bandwidth_utilization is not None and bandwidth_utilization >= 85:
        warnings.append(f"Канал загружен на {bandwidth_utilization:.2f}%.")
    over_capacity = int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0)
    if over_capacity > 0:
        warnings.append(f"Текущий поток выше лимита канала на {format_rate(over_capacity)}.")
    return warnings


def _render_legacy_dashboard(summary: Dict[str, object]) -> str:
    peers = summary["peers"]
    warnings = summary["warnings"]
    vpn = summary["vpn"]
    server = summary["server"]
    server_info = summary["server_info"]
    periods = summary.get("periods", {})
    ping = server["ping"]
    sample_window = vpn.get("sample_window_seconds") or 0
    status_class = "warn" if warnings else "ok"
    status_label = "ВНИМАНИЕ" if warnings else "OK"

    def esc(value: object) -> str:
        return html.escape(str(value))

    rows = []
    for peer in peers:
        handshake_sort = peer["handshake_age_seconds"] if peer["handshake_age_seconds"] is not None else 999999999
        active_class = "active" if peer["is_active"] else "inactive"
        active_label = "активен" if peer["is_active"] else "нет связи"
        share_percent = max(0.0, min(float(peer.get("current_share_percent", 0.0) or 0.0), 100.0))
        rows.append(
            "<tr "
            f'class="peer-row {active_class}" '
            f'data-sort-name="{esc(str(peer["name"]).lower())}" '
            f'data-sort-vpn_ip="{peer["vpn_ip_sort"]}" '
            f'data-sort-handshake_age_seconds="{handshake_sort}" '
            f'data-sort-is_active="{1 if peer["is_active"] else 0}" '
            f'data-sort-current_total_bps="{peer["current_total_bps"]}" '
            f'data-sort-current_rx_bps="{peer["current_rx_bps"]}" '
            f'data-sort-current_tx_bps="{peer["current_tx_bps"]}" '
            f'data-sort-daily_avg_total_bps="{peer["daily_avg_total_bps"]}" '
            f'data-sort-current_share_percent="{peer["current_share_percent"]}" '
            f'data-sort-today_bytes="{peer["today_bytes"]}" '
            f'data-sort-total_bytes="{peer["total_bytes"]}">'
            f"<td><span class='peer-name'>{esc(peer['name'])}</span></td>"
            f"<td>{esc(peer['vpn_ip'])}</td>"
            f"<td>{esc(peer['handshake_age'])}</td>"
            f"<td><span class='status-dot {active_class}'></span>{active_label}</td>"
            f"<td>{esc(format_rate(peer['current_total_bps']))}</td>"
            f"<td>{esc(format_rate(peer['current_rx_bps']))}</td>"
            f"<td>{esc(format_rate(peer['current_tx_bps']))}</td>"
            "<td>"
            f"<span class='share-cell'><span class='share-track'><span class='share-fill' style='width: {share_percent:.2f}%'></span></span>"
            f"<span>{esc(format_percent(peer['current_share_percent']))}</span></span>"
            "</td>"
            f"<td class='day-col'>{esc(format_rate(peer['daily_avg_total_bps']))}</td>"
            f"<td class='day-col'>{esc(format_bytes(peer['today_bytes']))}</td>"
            f"<td class='all-col'>{esc(format_bytes(peer['total_bytes']))}</td>"
            "</tr>"
        )

    warning_html = "".join(f"<li>{esc(item)}</li>" for item in warnings) or "<li>Предупреждений нет.</li>"
    server_notes = "".join(f"<li>{esc(item)}</li>" for item in server_info.get("notes", [])) or "<li>Нет заметок.</li>"
    billing_url = server_info.get("billing_url", "")
    billing_html = (
        f'<a href="{esc(billing_url)}" target="_blank" rel="noreferrer">{esc(billing_url)}</a>'
        if billing_url
        else "не заполнено"
    )
    provider_site = server_info.get("provider_site", "")
    provider_site_html = (
        f'<a href="{esc(provider_site)}" target="_blank" rel="noreferrer">{esc(provider_site)}</a>'
        if provider_site
        else "не заполнено"
    )
    day_period = periods.get("current_day", {})
    accounting_period = periods.get("accounting", {})
    day_range = f"{format_timestamp(day_period.get('started_at'))} -> {format_timestamp(day_period.get('ended_at'))}"
    accounting_range = (
        f"{format_timestamp(accounting_period.get('started_at'))} -> {format_timestamp(accounting_period.get('ended_at'))}"
    )
    bandwidth = server.get("bandwidth", {})
    current_total_bps = int(bandwidth.get("current_bytes_per_sec", 0) or 0)
    current_rx_bps = int(vpn.get("current_rx_bps", 0) or 0)
    current_tx_bps = int(vpn.get("current_tx_bps", 0) or 0)
    bandwidth_capacity_bps = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    bandwidth_utilization = bandwidth.get("utilization_percent")
    bandwidth_headroom_bps = int(bandwidth.get("headroom_bytes_per_sec", 0) or 0)
    bandwidth_daily = bandwidth.get("daily", {})
    bandwidth_source = bandwidth.get("source", "")
    bandwidth_interface = bandwidth.get("interface", "")
    bandwidth_limit_label = format_rate(bandwidth_capacity_bps) if bandwidth_capacity_bps else "н/д"
    bandwidth_utilization_label = format_percent(bandwidth_utilization)
    bandwidth_headroom_label = format_rate(bandwidth_headroom_bps) if bandwidth_capacity_bps else "н/д"
    bandwidth_day_average_label = format_rate(float(bandwidth_daily.get("average_current_total_bps", 0.0) or 0.0))
    bandwidth_day_peak_label = format_rate(float(bandwidth_daily.get("peak_current_total_bps", 0) or 0))
    bandwidth_day_peak_utilization_label = format_percent(bandwidth_daily.get("peak_utilization_percent"))
    bandwidth_context = bandwidth_source or bandwidth_interface or "auto"
    bandwidth_state = describe_bandwidth_state(
        bandwidth_capacity_bps,
        bandwidth_utilization if bandwidth_utilization is not None else None,
        int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0),
    )
    bandwidth_bar_width = float(bandwidth_state["bar_width_percent"])
    if bandwidth_capacity_bps > 0 and current_total_bps > 0:
        bandwidth_bar_width = max(bandwidth_bar_width, 2.0)
    bandwidth_bar_width = min(bandwidth_bar_width, 100.0)
    bandwidth_current_of_limit = (
        f"{format_rate(current_total_bps)} / {bandwidth_limit_label}" if bandwidth_capacity_bps else format_rate(current_total_bps)
    )

    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Панель Amnezia VPN</title>
  <style>
    :root {{
      --bg: #0a0f14;
      --panel: #111a22;
      --panel-alt: #17232d;
      --line: #293946;
      --fg: #eef5f7;
      --muted: #93a8b4;
      --ok: #38d996;
      --ok-soft: #102f24;
      --cyan: #52b7ff;
      --warn: #f2c46d;
      --warn-soft: #2b2113;
      --danger: #ff8a98;
      --danger-soft: #2a151d;
      --shadow: 0 18px 44px rgba(0, 0, 0, 0.28);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font: 14px/1.45 "Segoe UI", Roboto, Arial, sans-serif;
    }}
    .shell {{
      width: min(1760px, 100%);
      margin: 0 auto;
      padding: 18px;
    }}
    h1, h2 {{ margin: 0; }}
    h1 {{ font-size: clamp(22px, 2.2vw, 32px); line-height: 1.08; }}
    h2 {{ margin: 22px 0 10px; font-size: 16px; }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 16px 18px;
      background: var(--panel-alt);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }}
    .top-meta {{
      margin-top: 6px;
      color: var(--muted);
      font: 12px/1.4 Consolas, "Liberation Mono", Menlo, monospace;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 14px;
      box-shadow: var(--shadow);
    }}
    .panel strong {{
      display: block;
      margin-bottom: 8px;
      color: var(--fg);
      font-size: 13px;
    }}
    .server-info-panel {{
      grid-column: 1 / -1;
      column-count: 3;
      column-gap: 28px;
    }}
    .server-info-panel strong {{
      column-span: all;
    }}
    .status {{
      display: inline-block;
      padding: 6px 10px;
      border: 1px solid currentColor;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .status.ok {{ color: var(--ok); }}
    .status.warn {{ color: var(--warn); }}
    .status.danger {{ color: var(--danger); }}
    .traffic-banner {{
      margin: 14px 0 14px;
      padding: 18px;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, #111a22 0%, #0f181f 58%, #10231d 100%);
      box-shadow: var(--shadow);
    }}
    .traffic-banner-top {{
      display: flex;
      gap: 12px;
      align-items: baseline;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .traffic-title {{
      font-size: 12px;
      letter-spacing: 0.04em;
      color: var(--muted);
    }}
    .traffic-value {{
      font: 700 clamp(30px, 5vw, 54px)/1 Consolas, "Liberation Mono", Menlo, monospace;
      font-weight: 700;
      margin: 6px 0;
    }}
    .traffic-subvalue {{
      color: var(--muted);
      font: 12px/1.4 Consolas, "Liberation Mono", Menlo, monospace;
    }}
    .traffic-state {{
      border: 1px solid currentColor;
      padding: 7px 10px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .traffic-state.ok {{ color: var(--ok); }}
    .traffic-state.warn {{ color: var(--warn); }}
    .traffic-state.danger {{ color: var(--danger); }}
    .traffic-state.muted {{ color: var(--muted); }}
    .traffic-meter {{
      margin: 16px 0 10px;
      height: 12px;
      background: #0b141b;
      border: 1px solid var(--line);
      overflow: hidden;
    }}
    .traffic-fill {{
      height: 100%;
      width: 0%;
      background: var(--ok);
    }}
    .traffic-fill.warn {{ background: var(--warn); }}
    .traffic-fill.danger {{ background: var(--danger); }}
    .traffic-fill.muted {{ background: var(--muted); }}
    .peer-toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin: 8px 0 12px;
    }}
    .peer-search {{
      min-width: min(420px, 100%);
      flex: 1;
      border: 1px solid var(--line);
      background: #0b141b;
      color: var(--fg);
      padding: 9px 11px;
      font: inherit;
      outline: none;
    }}
    .peer-search:focus {{ border-color: var(--cyan); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{
      background: var(--panel-alt);
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    tbody tr.active {{ background: #10251f; }}
    tbody tr.inactive {{ color: var(--muted); }}
    tbody tr:hover {{ background: #17232d; }}
    .peer-name {{ font-weight: 700; color: var(--fg); }}
    .status-dot {{
      display: inline-block;
      width: 8px;
      height: 8px;
      margin-right: 8px;
      border-radius: 50%;
      background: var(--muted);
      vertical-align: 1px;
    }}
    .status-dot.active {{ background: var(--ok); box-shadow: 0 0 0 3px var(--ok-soft); }}
    .status-dot.inactive {{ background: var(--muted); }}
    .share-cell {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 116px;
    }}
    .share-track {{
      display: inline-block;
      width: 54px;
      height: 6px;
      background: #0b141b;
      border: 1px solid var(--line);
      overflow: hidden;
    }}
    .share-fill {{
      display: block;
      height: 100%;
      background: var(--cyan);
    }}
    .muted {{ color: var(--muted); }}
    ul {{ margin: 0; padding-left: 18px; }}
    .table-wrap {{ overflow-x: auto; }}
    .hint {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    a {{ color: var(--cyan); }}
    .sort-btn {{
      border: 0;
      background: transparent;
      padding: 0;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }}
    .sort-btn::after {{
      content: " <> ";
      color: var(--muted);
    }}
    .sort-btn[data-order="asc"]::after {{ content: " ^"; }}
    .sort-btn[data-order="desc"]::after {{ content: " v"; }}
    .view-switch {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .view-btn {{
      border: 1px solid var(--line);
      background: #0b141b;
      color: inherit;
      padding: 8px 10px;
      font: inherit;
      cursor: pointer;
    }}
    .view-btn.active {{
      border-color: var(--cyan);
      color: var(--fg);
      font-weight: 700;
    }}
    .empty-row td {{ color: var(--muted); text-align: center; }}
    .is-hidden {{
      display: none;
    }}
    @media (max-width: 760px) {{
      .shell {{ padding: 10px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; }}
      .traffic-value {{ font-size: 34px; }}
      .server-info-panel {{ column-count: 1; }}
      th, td {{ padding: 7px 8px; }}
    }}
  </style>
</head>
<body>
<main class="shell">
  <header class="topbar">
    <div>
      <h1>Панель Amnezia VPN</h1>
      <div class="top-meta">Обновлено: {updated_at} | {timezone} | окно скорости: {sample_window} сек. | Autostop VPN Shell</div>
    </div>
    <span class="status {status_class}">{status_label}</span>
  </header>

  <div class="traffic-banner">
    <div class="traffic-banner-top">
      <div>
        <div class="traffic-title">Загрузка канала</div>
        <div class="traffic-value">{bandwidth_utilization}</div>
        <div class="traffic-subvalue">{bandwidth_current_of_limit}</div>
      </div>
      <div class="traffic-state {bandwidth_state_class}">{bandwidth_state_label}</div>
    </div>
    <div class="traffic-meter" aria-label="Загрузка канала">
      <div class="traffic-fill {bandwidth_state_class}" style="width: {bandwidth_bar_width}%"></div>
    </div>
    <div class="muted">{bandwidth_state_note}</div>
  </div>

  <div class="grid">
    <div class="panel">
      <strong>VPN</strong><br>
      Интерфейс: {interface}<br>
      UDP-порт: {listen_port}<br>
      Всего пиров: {total_peers}<br>
      Активных ({active_window}): {active_connections}
      <div class="hint">Активные: у кого handshake не старше этого окна.</div>
    </div>
    <div class="panel">
      <strong>Сервер</strong><br>
      Нагрузка: {load1} / {load5} / {load15}<br>
      Память занято: {mem_used} ({mem_used_pct})<br>
      Диск занято: {disk_used} ({disk_used_pct})<br>
      Аптайм: {uptime}
      <div class="hint">Нагрузка: среднее за 1, 5 и 15 минут.</div>
    </div>
    <div class="panel">
      <strong>Канал</strong><br>
      Текущий поток: {current_total}<br>
      Средний поток за день: {bandwidth_day_average}<br>
      Пик за день: {bandwidth_day_peak} ({bandwidth_day_peak_utilization})<br>
      Приём / передача: {current_rx} / {current_tx}<br>
      Лимит: {bandwidth_limit}<br>
      Загрузка: {bandwidth_utilization}<br>
      Запас: {bandwidth_headroom}
      <div class="hint">Лимит берется из server_info.json или скорости сетевого интерфейса. Источник: {bandwidth_context}.</div>
    </div>
    <div class="panel">
      <strong>Сеть</strong><br>
      Ping-цель: {ping_target}<br>
      Средняя задержка: {latency}<br>
      Потери: {packet_loss}<br>
      Контейнер: {container_status}
      <div class="hint">Потери и задержка считаются обычным ping от сервера.</div>
    </div>
    <div class="panel">
      <strong>Периоды</strong><br>
      Текущие сутки: {day_range}<br>
      Весь период учёта: {accounting_range}
      <div class="hint">В таблице можно переключать режим просмотра по этим периодам.</div>
    </div>
    <div class="panel server-info-panel">
      <strong>{server_info_title}</strong><br>
      Назначение: {server_role}<br>
      Провайдер: {provider_name}<br>
      Сайт провайдера: {provider_site}<br>
      Биллинг: {billing_url}<br>
      Как оплачивать: {payment_note}<br>
      Где куплен: {purchase_note}<br>
      IP: {public_ip}<br>
      Хостнейм: {hostname}<br>
      Домен: {domain}<br>
      SSH: {ssh_user}:{ssh_port}<br>
      ОС: {server_os}<br>
      Проект: {project_path}<br>
      VPN-контейнер: {vpn_container}
    </div>
  </div>

  <h2>Предупреждения</h2>
  <div class="panel">
    <ul>{warning_html}</ul>
  </div>

  <h2>Заметки о сервере</h2>
  <div class="panel">
    <ul>{server_notes}</ul>
  </div>

  <h2>Пиры</h2>
  <div class="peer-toolbar">
    <input id="peer-search" class="peer-search" type="search" placeholder="Поиск по имени, IP, статусу или локации">
    <div class="view-switch">
      <button type="button" class="view-btn" data-mode="day">Текущие сутки</button>
      <button type="button" class="view-btn" data-mode="all">Весь период</button>
    </div>
  </div>
  <p class="muted" id="table-period-note"></p>
  <div class="table-wrap">
    <table id="peers-table">
      <thead>
        <tr>
          <th><button type="button" class="sort-btn" data-key="name" data-type="text">Имя</button></th>
          <th><button type="button" class="sort-btn" data-key="vpn_ip" data-type="number">VPN IP</button></th>
          <th><button type="button" class="sort-btn" data-key="handshake_age_seconds" data-type="number">Последний handshake</button></th>
          <th><button type="button" class="sort-btn" data-key="is_active" data-type="number">Активен</button></th>
          <th><button type="button" class="sort-btn" data-key="current_total_bps" data-type="number">Текущая</button></th>
          <th><button type="button" class="sort-btn" data-key="current_rx_bps" data-type="number">Вход</button></th>
          <th><button type="button" class="sort-btn" data-key="current_tx_bps" data-type="number">Выход</button></th>
          <th><button type="button" class="sort-btn" data-key="current_share_percent" data-type="number">Доля потока</button></th>
          <th class="day-col"><button type="button" class="sort-btn" data-key="daily_avg_total_bps" data-type="number">Средняя за день</button></th>
          <th class="day-col"><button type="button" class="sort-btn" data-key="today_bytes" data-type="number">Сегодня</button></th>
          <th class="all-col"><button type="button" class="sort-btn" data-key="total_bytes" data-type="number">Всего</button></th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
  <script>
    (function () {{
      const table = document.getElementById("peers-table");
      if (!table) return;
      const tbody = table.querySelector("tbody");
      if (!tbody) return;
      const buttons = Array.from(table.querySelectorAll(".sort-btn"));
      const modeButtons = Array.from(document.querySelectorAll(".view-btn"));
      const searchInput = document.getElementById("peer-search");
      const periodNote = document.getElementById("table-period-note");
      const dayRange = {day_range_json};
      const accountingRange = {accounting_range_json};

      function sortRows(key, type, order) {{
        const rows = Array.from(tbody.querySelectorAll("tr"));
        rows.sort((leftRow, rightRow) => {{
          let left = leftRow.getAttribute("data-sort-" + key) || "";
          let right = rightRow.getAttribute("data-sort-" + key) || "";
          if (type === "number") {{
            left = Number(left);
            right = Number(right);
          }}
          if (left < right) return order === "asc" ? -1 : 1;
          if (left > right) return order === "asc" ? 1 : -1;
          return 0;
        }});
        rows.forEach((row) => tbody.appendChild(row));
        applyPeerFilter();
      }}

      function applyPeerFilter() {{
        const query = (searchInput && searchInput.value ? searchInput.value : "").trim().toLowerCase();
        Array.from(tbody.querySelectorAll("tr")).forEach((row) => {{
          if (row.classList.contains("empty-row")) return;
          const text = row.textContent.toLowerCase();
          row.style.display = !query || text.includes(query) ? "" : "none";
        }});
      }}

      function applyMode(mode) {{
        const showDay = mode === "day";
        document.querySelectorAll(".day-col").forEach((node) => node.classList.toggle("is-hidden", !showDay));
        document.querySelectorAll(".all-col").forEach((node) => node.classList.toggle("is-hidden", showDay));
        modeButtons.forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
        if (periodNote) {{
          periodNote.textContent = showDay
            ? "Показан режим: текущие сутки (" + dayRange + ")."
            : "Показан режим: весь период учёта (" + accountingRange + ").";
        }}
        const defaultKey = showDay ? "today_bytes" : "total_bytes";
        const defaultButton = table.querySelector('.sort-btn[data-key="' + defaultKey + '"]');
        if (defaultButton) {{
          buttons.forEach((item) => item.removeAttribute("data-order"));
          defaultButton.dataset.order = "desc";
          sortRows(defaultButton.dataset.key, defaultButton.dataset.type, "desc");
        }}
      }}

      buttons.forEach((button) => {{
        button.addEventListener("click", () => {{
          const nextOrder = button.dataset.order === "desc" ? "asc" : "desc";
          buttons.forEach((item) => item.removeAttribute("data-order"));
          button.dataset.order = nextOrder;
          sortRows(button.dataset.key, button.dataset.type, nextOrder);
        }});
      }});

      modeButtons.forEach((button) => {{
        button.addEventListener("click", () => applyMode(button.dataset.mode));
      }});
      if (searchInput) {{
        searchInput.addEventListener("input", applyPeerFilter);
      }}

      applyMode("all");
    }})();
  </script>
</main>
</body>
</html>
""".format(
        updated_at=esc(summary["updated_at"]),
        timezone=esc(summary["timezone"]),
        sample_window=sample_window,
        status_class=status_class,
        status_label=status_label,
        interface=esc(vpn["interface"]),
        listen_port=esc(vpn["listen_port"]),
        total_peers=esc(vpn["total_peers"]),
        active_window=esc(format_age(vpn["active_window_seconds"])),
        active_connections=esc(vpn["active_connections"]),
        load1=esc(server["loadavg"]["1m"]),
        load5=esc(server["loadavg"]["5m"]),
        load15=esc(server["loadavg"]["15m"]),
        mem_used=esc(format_bytes(server["memory"]["used_bytes"])),
        mem_used_pct=esc(format_percent(server["memory"]["used_percent"])),
        disk_used=esc(format_bytes(server["disk_root"]["used_bytes"])),
        disk_used_pct=esc(format_percent(server["disk_root"]["used_percent"])),
        uptime=esc(format_age(server["uptime_seconds"])),
        current_total=esc(format_rate(current_total_bps)),
        bandwidth_day_average=esc(bandwidth_day_average_label),
        bandwidth_day_peak=esc(bandwidth_day_peak_label),
        current_rx=esc(format_rate(current_rx_bps)),
        current_tx=esc(format_rate(current_tx_bps)),
        bandwidth_limit=esc(bandwidth_limit_label),
        bandwidth_utilization=esc(bandwidth_utilization_label),
        bandwidth_headroom=esc(bandwidth_headroom_label),
        bandwidth_current_of_limit=esc(bandwidth_current_of_limit),
        bandwidth_state_class=esc(bandwidth_state["class"]),
        bandwidth_state_label=esc(bandwidth_state["label"]),
        bandwidth_state_note=esc(bandwidth_state["note"]),
        bandwidth_bar_width=esc(f"{bandwidth_bar_width:.2f}"),
        bandwidth_day_peak_utilization=esc(bandwidth_day_peak_utilization_label),
        bandwidth_context=esc(bandwidth_context),
        ping_target=esc(ping["target"]),
        latency=esc(
            f"{ping['latency_avg_ms']:.2f} мс" if ping["latency_avg_ms"] is not None else "н/д"
        ),
        packet_loss=esc(format_percent(ping["packet_loss_percent"])),
        container_status=esc(translate_container_status(summary["container"]["status"])),
        server_info_title=esc(server_info.get("title", "Данные сервера")),
        server_role=esc(server_info.get("server_role", "")),
        provider_name=esc(server_info.get("provider_name", "")),
        provider_site=provider_site_html,
        billing_url=billing_html,
        payment_note=esc(server_info.get("payment_note", "")),
        purchase_note=esc(server_info.get("purchase_note", "")),
        day_range=esc(day_range),
        accounting_range=esc(accounting_range),
        day_range_json=json.dumps(day_range, ensure_ascii=False),
        accounting_range_json=json.dumps(accounting_range, ensure_ascii=False),
        public_ip=esc(server_info.get("public_ip", "")),
        hostname=esc(server_info.get("hostname", "")),
        domain=esc(server_info.get("domain", "")),
        ssh_user=esc(server_info.get("ssh_user", "")),
        ssh_port=esc(server_info.get("ssh_port", "")),
        server_os=esc(server_info.get("os", "")),
        project_path=esc(server_info.get("project_path", "")),
        vpn_container=esc(server_info.get("vpn_container", "")),
        warning_html=warning_html,
        server_notes=server_notes,
        rows="".join(rows) or "<tr class='empty-row'><td colspan='11'>Пиры не найдены.</td></tr>",
    )


def render_dashboard(summary: Dict[str, object]) -> str:
    peers = summary.get("peers", [])
    if not isinstance(peers, list):
        peers = []
    warnings = summary.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    vpn = summary.get("vpn", {}) if isinstance(summary.get("vpn", {}), dict) else {}
    server = summary.get("server", {}) if isinstance(summary.get("server", {}), dict) else {}
    server_info = summary.get("server_info", {}) if isinstance(summary.get("server_info", {}), dict) else {}
    periods = summary.get("periods", {}) if isinstance(summary.get("periods", {}), dict) else {}
    container = summary.get("container", {}) if isinstance(summary.get("container", {}), dict) else {}
    bandwidth = server.get("bandwidth", {}) if isinstance(server.get("bandwidth", {}), dict) else {}
    daily = bandwidth.get("daily", {}) if isinstance(bandwidth.get("daily", {}), dict) else {}
    ping = server.get("ping", {}) if isinstance(server.get("ping", {}), dict) else {}
    loadavg = server.get("loadavg", {}) if isinstance(server.get("loadavg", {}), dict) else {}
    memory = server.get("memory", {}) if isinstance(server.get("memory", {}), dict) else {}
    disk = server.get("disk_root", {}) if isinstance(server.get("disk_root", {}), dict) else {}

    def esc(value: object) -> str:
        return html.escape(str(value if value is not None else ""))

    def location_bucket(value: object) -> str:
        label = str(value or "").strip()
        if not label:
            return "нет данных"
        return label.split(",", 1)[0].strip() or label

    def percent_of(value: float, total: float) -> float:
        if total <= 0:
            return 0.0
        return max(0.0, min((value / total) * 100.0, 100.0))

    def peer_rate(peer: Dict[str, object], key: str) -> float:
        try:
            return float(peer.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    current_total_bps = int(bandwidth.get("current_bytes_per_sec", vpn.get("current_total_bps", 0)) or 0)
    current_rx_bps = int(vpn.get("current_rx_bps", 0) or 0)
    current_tx_bps = int(vpn.get("current_tx_bps", 0) or 0)
    capacity_bps = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    utilization_value = bandwidth.get("utilization_percent")
    utilization_float = float(utilization_value or 0.0) if utilization_value is not None else percent_of(current_total_bps, capacity_bps)
    headroom_bps = int(bandwidth.get("headroom_bytes_per_sec", 0) or 0)
    bandwidth_state = describe_bandwidth_state(
        capacity_bps,
        utilization_float if utilization_value is not None else None,
        int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0),
    )
    bandwidth_bar_width = float(bandwidth_state["bar_width_percent"])
    if capacity_bps > 0 and current_total_bps > 0:
        bandwidth_bar_width = max(bandwidth_bar_width, 2.0)
    bandwidth_bar_width = min(bandwidth_bar_width, 100.0)
    day_period = periods.get("current_day", {}) if isinstance(periods.get("current_day", {}), dict) else {}
    accounting_period = periods.get("accounting", {}) if isinstance(periods.get("accounting", {}), dict) else {}
    day_range = f"{format_timestamp(day_period.get('started_at'))} -> {format_timestamp(day_period.get('ended_at'))}"
    accounting_range = (
        f"{format_timestamp(accounting_period.get('started_at'))} -> {format_timestamp(accounting_period.get('ended_at'))}"
    )
    total_peers = int(vpn.get("total_peers", len(peers)) or 0)
    active_connections = int(vpn.get("active_connections", 0) or 0)
    offline_connections = max(total_peers - active_connections, 0)
    active_percent = (active_connections / total_peers * 100.0) if total_peers else 0.0
    offline_percent = (offline_connections / total_peers * 100.0) if total_peers else 0.0
    sample_window = vpn.get("sample_window_seconds") or 0
    current_total_label = format_rate(current_total_bps)
    current_rx_label = format_rate(current_rx_bps)
    current_tx_label = format_rate(current_tx_bps)
    bandwidth_limit_label = format_rate(capacity_bps) if capacity_bps else "н/д"
    bandwidth_headroom_label = format_rate(headroom_bps) if capacity_bps else "н/д"
    daily_average_bps = float(daily.get("average_current_total_bps", 0.0) or 0.0)
    daily_peak_bps = float(daily.get("peak_current_total_bps", 0.0) or 0.0)
    daily_average_label = format_rate(daily_average_bps)
    daily_peak_label = format_rate(daily_peak_bps)
    daily_peak_utilization_label = format_percent(daily.get("peak_utilization_percent"))
    top_peer = max(
        (peer for peer in peers if isinstance(peer, dict)),
        key=lambda peer: peer_rate(peer, "current_total_bps"),
        default={},
    )
    top_peer_name = str(top_peer.get("name", "—") or "—")
    top_peer_location = str(top_peer.get("endpoint_location", "") or "")
    top_peer_rate = format_rate(peer_rate(top_peer, "current_total_bps")) if top_peer else "—"
    top_peer_share = format_percent(top_peer.get("current_share_percent")) if top_peer else "—"
    location_options = sorted(
        {
            location_bucket(peer.get("endpoint_location", ""))
            for peer in peers
            if isinstance(peer, dict) and location_bucket(peer.get("endpoint_location", ""))
        }
    )
    location_options_html = "".join(f'<option value="{esc(item)}">{esc(item)}</option>' for item in location_options)

    def channel_row(title: str, value: str, ratio: float, tone: str) -> str:
        return (
            '<div class="channel-row">'
            f"<span>{esc(title)}</span><strong>{esc(value)}</strong>"
            f'<em>{ratio:.2f}%</em><b><i class="{tone}" style="width: {ratio:.2f}%"></i></b>'
            "</div>"
        )

    channel_rows = "".join(
        [
            channel_row("Входящий", current_rx_label, percent_of(current_rx_bps, capacity_bps), "blue"),
            channel_row("Исходящий", current_tx_label, percent_of(current_tx_bps, capacity_bps), "green"),
            channel_row("Пик за сегодня", daily_peak_label, percent_of(daily_peak_bps, capacity_bps), "yellow"),
            channel_row("Средний за сегодня", daily_average_label, percent_of(daily_average_bps, capacity_bps), "muted"),
            channel_row("Лимит канала", bandwidth_limit_label, 100.0 if capacity_bps else 0.0, "muted"),
            channel_row("Запас канала", bandwidth_headroom_label, percent_of(headroom_bps, capacity_bps), "green"),
        ]
    )

    def peer_detail_payload(peer: Dict[str, object], active_label: str, location: str, channel: str) -> str:
        payload = {
            "name": peer.get("name", "—"),
            "vpnIp": peer.get("vpn_ip", "—"),
            "status": active_label,
            "location": location,
            "channel": channel,
            "endpoint": peer.get("endpoint", "—"),
            "handshake": peer.get("handshake_age") or format_age(peer.get("handshake_age_seconds")),
            "rx": format_rate(peer_rate(peer, "current_rx_bps")),
            "tx": format_rate(peer_rate(peer, "current_tx_bps")),
            "total": format_rate(peer_rate(peer, "current_total_bps")),
            "share": format_percent(peer.get("current_share_percent")),
            "today": format_bytes(int(peer.get("today_bytes", 0) or 0)),
            "all": format_bytes(int(peer.get("total_bytes", 0) or 0)),
            "key": peer.get("public_key_short", "—"),
        }
        return esc(json.dumps(payload, ensure_ascii=False))

    rows = []
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        handshake_sort = peer.get("handshake_age_seconds") if peer.get("handshake_age_seconds") is not None else 999999999
        active_class = "active" if peer.get("is_active") else "inactive"
        active_label = "Онлайн" if peer.get("is_active") else "Оффлайн"
        location = str(peer.get("endpoint_location", "") or "нет данных")
        location_filter = location_bucket(location)
        channel = str(vpn.get("interface", "") or "awg0")
        share_percent = max(0.0, min(float(peer.get("current_share_percent", 0.0) or 0.0), 100.0))
        detail_payload = peer_detail_payload(peer, active_label, location, channel)
        rows.append(
            "<tr "
            f'class="peer-row {active_class}" '
            f'data-active="{active_label}" '
            f'data-location="{esc(location_filter)}" '
            f'data-detail="{detail_payload}" '
            f'data-sort-name="{esc(str(peer.get("name", "")).lower())}" '
            f'data-sort-vpn_ip="{esc(peer.get("vpn_ip_sort", peer.get("vpn_ip", "")))}" '
            f'data-sort-handshake_age_seconds="{esc(handshake_sort)}" '
            f'data-sort-is_active="{1 if peer.get("is_active") else 0}" '
            f'data-sort-current_total_bps="{esc(peer.get("current_total_bps", 0))}" '
            f'data-sort-current_rx_bps="{esc(peer.get("current_rx_bps", 0))}" '
            f'data-sort-current_tx_bps="{esc(peer.get("current_tx_bps", 0))}" '
            f'data-sort-current_share_percent="{esc(peer.get("current_share_percent", 0))}" '
            f'data-sort-today_bytes="{esc(peer.get("today_bytes", 0))}" '
            f'data-sort-total_bytes="{esc(peer.get("total_bytes", 0))}">'
            f'<td><span class="status-dot {active_class}"></span></td>'
            f'<td><span class="peer-name">{esc(peer.get("name", ""))}</span></td>'
            f'<td>{esc(peer.get("vpn_ip", ""))}</td>'
            f"<td>{esc(location_filter)}</td>"
            f"<td>{esc(channel)}</td>"
            f'<td>{esc(peer.get("handshake_age") or format_age(peer.get("handshake_age_seconds")))}</td>'
            f'<td>{esc(format_rate(peer_rate(peer, "current_rx_bps")))}</td>'
            f'<td>{esc(format_rate(peer_rate(peer, "current_tx_bps")))}</td>'
            f'<td>{esc(format_rate(peer_rate(peer, "current_total_bps")))}</td>'
            "<td>"
            f'<span class="share-cell"><span class="share-track"><span class="share-fill" style="width: {share_percent:.2f}%"></span></span>'
            f'<span>{esc(format_percent(peer.get("current_share_percent")))}</span></span>'
            "</td>"
            f'<td class="day-metric">{esc(format_bytes(int(peer.get("today_bytes", 0) or 0)))}</td>'
            f'<td class="all-metric">{esc(format_bytes(int(peer.get("total_bytes", 0) or 0)))}</td>'
            "</tr>"
        )
    rows_html = "".join(rows) or '<tr class="empty-row"><td colspan="12">Пиры не найдены.</td></tr>'
    warning_html = "".join(f"<li>{esc(item)}</li>" for item in warnings) or "<li>Предупреждений нет.</li>"
    status_label = "Внимание" if warnings else "Норма"
    container_status = translate_container_status(str(container.get("status", "")))
    server_uptime = format_age(server.get("uptime_seconds"))
    latency = f'{float(ping.get("latency_avg_ms")):.2f} мс' if ping.get("latency_avg_ms") is not None else "н/д"
    packet_loss = format_percent(ping.get("packet_loss_percent"))
    mem_label = f'{format_bytes(memory.get("used_bytes", 0))} / {format_percent(memory.get("used_percent"))}'
    disk_label = f'{format_bytes(disk.get("used_bytes", 0))} / {format_percent(disk.get("used_percent"))}'
    server_notes = "".join(f"<li>{esc(item)}</li>" for item in server_info.get("notes", [])) or "<li>Нет заметок.</li>"
    top_event_items = [
        f"Снимок обновлен: {format_timestamp(summary.get('updated_at'))}",
        f"Контейнер: {container_status}",
        f"Лидер трафика: {top_peer_name}",
    ]
    if warnings:
        top_event_items.insert(0, f"Предупреждение: {warnings[0]}")
    top_events_html = "".join(f"<li>{esc(item)}</li>" for item in top_event_items[:5])

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Панель Amnezia VPN</title>
  <style>
    :root {{ --bg:#070d13; --panel:#101b25; --panel-2:#132231; --line:#263544; --line-soft:#1b2a38; --fg:#dce8f4; --muted:#93a4b7; --ok:#32d06f; --ok-soft:#0f2d22; --blue:#2f82ff; --cyan:#4cc9f0; --warn:#f5c542; --danger:#ff5b55; --shadow:0 18px 46px rgba(0,0,0,.28); }}
    * {{ box-sizing:border-box; }} html,body {{ width:100%; max-width:100%; overflow-x:hidden; }} body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.42 "Segoe UI",Inter,Arial,sans-serif; }} button,input,select {{ font:inherit; }}
    .shell {{ width:min(1760px,100%); max-width:100%; margin:0 auto; padding:8px 12px 12px; overflow:hidden; }} h1 {{ margin:0; font-size:22px; font-weight:500; }} h2 {{ margin:0; font-size:16px; font-weight:700; }}
    .app-header {{ min-height:50px; display:grid; grid-template-columns:1fr auto; gap:16px; align-items:center; border-bottom:1px solid var(--line); }}
    .top-status {{ display:flex; align-items:center; gap:28px; flex-wrap:wrap; color:var(--muted); }} .top-status strong {{ color:var(--fg); font-weight:600; }}
    .dot {{ display:inline-block; width:9px; height:9px; margin-right:7px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 3px var(--ok-soft); }}
    .icon-btn {{ width:32px; height:32px; border:1px solid var(--line); background:var(--panel); color:var(--fg); border-radius:4px; }}
    .dashboard-grid {{ display:grid; grid-template-columns:minmax(350px,38%) 1fr; gap:8px; margin-top:8px; }}
    .panel {{ min-width:0; background:linear-gradient(135deg,var(--panel) 0%,#0e1822 100%); border:1px solid var(--line); border-radius:6px; box-shadow:var(--shadow); }}
    .channel-panel {{ padding:14px 16px; }} .panel-title {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }} .muted {{ color:var(--muted); }}
    .channel-main {{ display:grid; grid-template-columns:1fr auto; align-items:end; gap:12px; margin:16px 0 8px; }} .channel-main strong {{ font:700 30px/1 Consolas,monospace; }} .channel-main em {{ font:700 25px/1 Consolas,monospace; color:var(--ok); font-style:normal; }}
    .traffic-meter {{ height:12px; border:1px solid var(--line); background:#1d2b38; overflow:hidden; border-radius:3px; }} .traffic-fill {{ display:block; height:100%; background:var(--ok); }} .traffic-fill.warn {{ background:var(--warn); }} .traffic-fill.danger {{ background:var(--danger); }}
    .channel-row {{ display:grid; grid-template-columns:1fr auto 64px 172px; gap:12px; align-items:center; padding:8px 0; border-top:1px solid var(--line-soft); }} .channel-row strong,.channel-row em {{ font:600 13px Consolas,monospace; font-style:normal; text-align:right; }} .channel-row b {{ height:8px; border:1px solid var(--line); background:#1b2a38; border-radius:3px; overflow:hidden; }} .channel-row i {{ display:block; height:100%; background:var(--ok); }} .channel-row i.blue {{ background:var(--blue); }} .channel-row i.yellow {{ background:var(--warn); }} .channel-row i.muted {{ background:#6f7f90; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(180px,1fr)); gap:8px; }} .kpi-card {{ min-height:222px; padding:14px 16px; }} .kpi-title {{ font-weight:600; margin-bottom:14px; }} .kpi-value {{ font:700 31px/1.05 Consolas,monospace; margin-bottom:6px; }} .kpi-sub {{ color:var(--muted); margin-bottom:22px; }} .kpi-line {{ display:flex; justify-content:space-between; gap:14px; padding:5px 0; color:var(--muted); }} .kpi-line span:first-child:before {{ content:""; display:inline-block; width:9px; height:9px; margin-right:8px; border-radius:50%; background:var(--ok); }} .kpi-line.warn span:first-child:before {{ background:var(--warn); }} .kpi-line.danger span:first-child:before {{ background:var(--danger); }}
    .traffic-panel {{ margin-top:8px; padding:14px 16px; display:grid; grid-template-columns:1fr 260px; gap:18px; }} .chart-box {{ min-height:166px; border-top:1px solid var(--line-soft); border-bottom:1px solid var(--line-soft); padding:14px 0; }} .chart-row {{ display:grid; grid-template-columns:94px 1fr 96px; gap:12px; align-items:center; margin:14px 0; color:var(--muted); }} .chart-track {{ height:12px; background:#172534; border:1px solid var(--line); border-radius:3px; overflow:hidden; }} .chart-track i {{ display:block; height:100%; }} .chart-track .rx {{ background:var(--blue); }} .chart-track .tx {{ background:var(--ok); }} .chart-track .total {{ background:var(--cyan); }} .traffic-values dl {{ display:grid; grid-template-columns:1fr auto; gap:9px 14px; margin:10px 0 0; }} .traffic-values dt {{ color:var(--muted); }} .traffic-values dd {{ margin:0; font:600 13px Consolas,monospace; }}
    .workspace-grid {{ display:grid; grid-template-columns:minmax(0,1fr) 390px; gap:8px; margin-top:8px; }} .table-panel,.detail-panel {{ min-width:0; padding:12px; }} .peer-toolbar {{ display:grid; grid-template-columns:minmax(260px,1fr) 150px 150px 170px 96px auto; gap:8px; align-items:center; margin-bottom:10px; }} .peer-toolbar input,.peer-toolbar select,.peer-toolbar button {{ min-width:0; height:38px; border:1px solid var(--line); background:#0a131c; color:var(--fg); border-radius:4px; padding:0 10px; outline:none; }} .peer-count {{ color:var(--muted); text-align:right; white-space:nowrap; }}
    .table-wrap {{ width:100%; max-width:100%; overflow:auto; max-height:406px; border:1px solid var(--line); }} table {{ width:100%; border-collapse:collapse; min-width:1120px; background:#0f1822; }} th,td {{ padding:8px 10px; border-bottom:1px solid var(--line-soft); white-space:nowrap; text-align:left; }} th {{ position:sticky; top:0; z-index:1; background:#0c141d; color:var(--muted); font-weight:600; }} tbody tr {{ cursor:pointer; }} tbody tr.active {{ background:rgba(20,74,57,.45); }} tbody tr.inactive {{ color:var(--muted); }} tbody tr.selected {{ background:#174a83; color:var(--fg); }} tbody tr:hover {{ background:#162434; }}
    .peer-name {{ font-weight:700; color:var(--fg); }} .status-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--muted); }} .status-dot.active {{ background:var(--ok); box-shadow:0 0 0 3px var(--ok-soft); }} .status-dot.inactive {{ background:var(--danger); box-shadow:0 0 0 3px rgba(255,91,85,.12); }} .share-cell {{ display:inline-flex; gap:8px; align-items:center; min-width:116px; }} .share-track {{ width:54px; height:6px; background:#0a131c; border:1px solid var(--line); overflow:hidden; }} .share-fill {{ display:block; height:100%; background:var(--blue); }} .sort-btn {{ border:0; background:transparent; color:inherit; padding:0; cursor:pointer; }} .sort-btn:after {{ content:" <>"; color:#65778a; }} .sort-btn[data-order="asc"]:after {{ content:" ^"; }} .sort-btn[data-order="desc"]:after {{ content:" v"; }}
    .detail-title {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }} .detail-name {{ font:700 22px/1.1 Consolas,monospace; margin:6px 0; }} .detail-status {{ color:var(--ok); font-weight:700; margin-bottom:14px; }} .detail-grid {{ display:grid; grid-template-columns:112px 1fr; gap:7px 12px; }} .detail-grid dt {{ color:var(--muted); }} .detail-grid dd {{ margin:0; }} .event-list {{ margin:16px -12px 0; padding:14px 12px 0; border-top:1px solid var(--line); }} .event-list ul,.notes ul {{ margin:0; padding-left:18px; }} .notes {{ margin-top:8px; padding:12px; }} .is-hidden {{ display:none; }}
    @media (max-width:1100px) {{ .dashboard-grid,.workspace-grid,.traffic-panel {{ grid-template-columns:1fr; }} .kpi-grid {{ grid-template-columns:repeat(2,1fr); }} .peer-toolbar {{ grid-template-columns:1fr 1fr; }} .peer-count {{ text-align:left; }} }}
    @media (max-width:680px) {{ .shell {{ padding:8px; }} .app-header,.top-status {{ display:block; }} .top-status>* {{ display:inline-block; margin:6px 12px 0 0; }} .kpi-grid,.peer-toolbar {{ grid-template-columns:1fr; }} .channel-row {{ grid-template-columns:1fr auto; }} .channel-row em,.channel-row b {{ display:none; }} }}
  </style>
</head>
<body>
<main class="shell">
  <header class="app-header"><h1>VPN Мониторинг</h1><div class="top-status"><span><span class="dot"></span>Система: <strong>{esc(status_label)}</strong></span><span><span class="dot"></span>Сервер VPN: <strong>Онлайн</strong></span><span>Время: <strong>{esc(format_timestamp(summary.get("updated_at")))}</strong></span><span>Аптайм: <strong>{esc(server_uptime)}</strong></span><span>Обновление: <strong>{esc(sample_window)} сек</strong></span><span>Источник: <strong>Autostop VPN Shell</strong></span><button class="icon-btn" type="button" onclick="location.reload()" title="Обновить">R</button></div></header>
  <section class="dashboard-grid">
    <article class="panel channel-panel"><div class="panel-title"><h2>Использование канала</h2><span class="muted">awg0</span></div><div class="muted">Основной канал: {esc(bandwidth.get("interface") or vpn.get("interface") or "awg0")} / autodetected</div><div class="channel-main"><strong>{esc(current_total_label)}</strong><em>{esc(format_percent(utilization_float))}</em></div><div class="traffic-meter"><span class="traffic-fill {esc(bandwidth_state["class"])}" style="width: {bandwidth_bar_width:.2f}%"></span></div>{channel_rows}<p class="muted">Источник: {esc(bandwidth.get("source") or "auto")}</p></article>
    <div class="kpi-grid">
      <article class="panel kpi-card"><div class="kpi-title">Пиры</div><div class="kpi-value">{esc(total_peers)}</div><div class="kpi-sub">Всего</div><div class="kpi-line"><span>Онлайн</span><strong>{esc(active_connections)} ({active_percent:.1f}%)</strong></div><div class="kpi-line danger"><span>Оффлайн</span><strong>{esc(offline_connections)} ({offline_percent:.1f}%)</strong></div><div class="kpi-line warn"><span>Предупр.</span><strong>{esc(len(warnings))}</strong></div></article>
      <article class="panel kpi-card"><div class="kpi-title">VPN-сервер</div><div class="kpi-value">1</div><div class="kpi-sub">Всего</div><div class="kpi-line"><span>Онлайн</span><strong>1 (100%)</strong></div><div class="kpi-line warn"><span>Ping</span><strong>{esc(latency)}</strong></div><div class="kpi-line"><span>Потери</span><strong>{esc(packet_loss)}</strong></div></article>
      <article class="panel kpi-card"><div class="kpi-title">Снимки конфигурации</div><div class="kpi-value">1</div><div class="kpi-sub">Текущий snapshot</div><div class="kpi-line"><span>Успешно</span><strong>1 (100%)</strong></div><div class="kpi-line danger"><span>С ошибками</span><strong>0 (0%)</strong></div><div class="kpi-line warn"><span>Устарели</span><strong>0 (0%)</strong></div></article>
      <article class="panel kpi-card"><div class="kpi-title">Лидер трафика</div><div class="kpi-value">{esc(top_peer_name)}</div><div class="kpi-sub">{esc(location_bucket(top_peer_location))}</div><div class="kpi-line"><span>Поток</span><strong>{esc(top_peer_rate)}</strong></div><div class="kpi-line"><span>Доля</span><strong>{esc(top_peer_share)}</strong></div><div class="kpi-line"><span>Статус</span><strong>активен</strong></div></article>
    </div>
  </section>
  <section class="panel traffic-panel traffic-banner"><div><div class="panel-title"><h2>Трафик (текущий снимок и суточные ориентиры)</h2><span class="muted">окно {esc(sample_window)} сек</span></div><div class="chart-box"><div class="chart-row"><span>Входящий</span><b class="chart-track"><i class="rx" style="width: {percent_of(current_rx_bps, max(current_total_bps, 1)):.2f}%"></i></b><strong>{esc(current_rx_label)}</strong></div><div class="chart-row"><span>Исходящий</span><b class="chart-track"><i class="tx" style="width: {percent_of(current_tx_bps, max(current_total_bps, 1)):.2f}%"></i></b><strong>{esc(current_tx_label)}</strong></div><div class="chart-row"><span>Всего</span><b class="chart-track"><i class="total" style="width: {percent_of(current_total_bps, max(daily_peak_bps, current_total_bps, 1)):.2f}%"></i></b><strong>{esc(current_total_label)}</strong></div></div></div><aside class="traffic-values"><h2>Текущие значения</h2><dl><dt>Входящий</dt><dd>{esc(current_rx_label)}</dd><dt>Исходящий</dt><dd>{esc(current_tx_label)}</dd><dt>Всего</dt><dd>{esc(current_total_label)}</dd><dt>Пиковая нагрузка сегодня</dt><dd>{esc(daily_peak_label)} / {esc(daily_peak_utilization_label)}</dd><dt>Средняя нагрузка сегодня</dt><dd>{esc(daily_average_label)}</dd><dt>Загрузка канала</dt><dd>{esc(format_percent(utilization_float))}</dd></dl></aside></section>
  <section class="workspace-grid">
    <article class="panel table-panel"><div class="peer-toolbar"><input id="peer-search" class="peer-search" type="search" placeholder="Поиск по имени, адресу, площадке..."><select id="status-filter"><option>Статус: Все</option><option>Статус: Онлайн</option><option>Статус: Оффлайн</option></select><select id="period-filter"><option value="day">Период: Сегодня</option><option value="all">Период: Весь</option></select><select id="location-filter"><option value="Все">Площадка: Все</option>{location_options_html}</select><button id="reset-filters" type="button">Сбросить</button><div class="peer-count" id="peer-count">Пиров: {esc(total_peers)}</div></div><div class="table-wrap"><table id="peers-table"><thead><tr><th>Статус</th><th><button class="sort-btn" data-key="name" data-type="text">Пир</button></th><th><button class="sort-btn" data-key="vpn_ip" data-type="text">Виртуальный IP</button></th><th>Площадка</th><th>Канал</th><th><button class="sort-btn" data-key="handshake_age_seconds" data-type="number">Время отклика</button></th><th><button class="sort-btn" data-key="current_rx_bps" data-type="number">Входящий</button></th><th><button class="sort-btn" data-key="current_tx_bps" data-type="number">Исходящий</button></th><th><button class="sort-btn" data-key="current_total_bps" data-type="number">Всего</button></th><th><button class="sort-btn" data-key="current_share_percent" data-type="number">Доля</button></th><th class="day-metric"><button class="sort-btn" data-key="today_bytes" data-type="number">Сегодня</button></th><th class="all-metric"><button class="sort-btn" data-key="total_bytes" data-type="number">Всего период</button></th></tr></thead><tbody>{rows_html}</tbody></table></div><p class="muted" id="period-note">Показан режим: текущие сутки ({esc(day_range)}).</p></article>
    <aside class="panel detail-panel"><div class="detail-title"><h2>Детали пира</h2><span class="muted">select row</span></div><div class="detail-name" id="detail-name">{esc(top_peer_name)}</div><div class="detail-status" id="detail-status">Онлайн</div><dl class="detail-grid"><dt>Виртуальный IP</dt><dd id="detail-vpnIp">—</dd><dt>Площадка</dt><dd id="detail-location">—</dd><dt>Канал</dt><dd id="detail-channel">—</dd><dt>Endpoint</dt><dd id="detail-endpoint">—</dd><dt>Handshake</dt><dd id="detail-handshake">—</dd><dt>Входящий</dt><dd id="detail-rx">—</dd><dt>Исходящий</dt><dd id="detail-tx">—</dd><dt>Всего</dt><dd id="detail-total">—</dd><dt>Доля</dt><dd id="detail-share">—</dd><dt>Сегодня</dt><dd id="detail-today">—</dd><dt>Весь период</dt><dd id="detail-all">—</dd></dl><div class="event-list"><h2>Последние события</h2><ul id="detail-events">{top_events_html}</ul></div></aside>
  </section>
  <section class="panel notes"><strong>Сервер</strong><span class="muted"> load {esc(loadavg.get("1m", "н/д"))} / {esc(loadavg.get("5m", "н/д"))} / {esc(loadavg.get("15m", "н/д"))}; memory {esc(mem_label)}; disk {esc(disk_label)}; project {esc(server_info.get("project_path", ""))}</span><ul>{warning_html}{server_notes}</ul></section>
</main>
<script>
(function() {{
  const table=document.getElementById("peers-table"); if(!table) return;
  const tbody=table.querySelector("tbody"); const rows=Array.from(tbody.querySelectorAll("tr.peer-row"));
  const search=document.getElementById("peer-search"); const statusFilter=document.getElementById("status-filter"); const periodFilter=document.getElementById("period-filter"); const locationFilter=document.getElementById("location-filter"); const reset=document.getElementById("reset-filters"); const peerCount=document.getElementById("peer-count"); const periodNote=document.getElementById("period-note");
  const dayRange={json.dumps(day_range, ensure_ascii=False)}; const accountingRange={json.dumps(accounting_range, ensure_ascii=False)};
  function statusValue() {{ return (statusFilter ? statusFilter.value : "Статус: Все").replace("Статус: ",""); }}
  function applyFilters() {{ const query=(search&&search.value?search.value:"").toLowerCase().trim(); const status=statusValue(); const location=locationFilter?locationFilter.value:"Все"; let visible=0; rows.forEach((row)=>{{ const show=(!query||row.textContent.toLowerCase().includes(query))&&(status==="Все"||row.dataset.active===status)&&(location==="Все"||row.dataset.location===location); row.style.display=show?"":"none"; if(show) visible+=1; }}); if(peerCount) peerCount.textContent="Показано: "+visible+" / {total_peers}"; }}
  function applyPeriod() {{ const mode=periodFilter?periodFilter.value:"day"; document.querySelectorAll(".day-metric").forEach((node)=>node.classList.toggle("is-hidden",mode!=="day")); document.querySelectorAll(".all-metric").forEach((node)=>node.classList.toggle("is-hidden",mode!=="all")); if(periodNote) periodNote.textContent=mode==="day"?"Показан режим: текущие сутки ("+dayRange+").":"Показан режим: весь период учета ("+accountingRange+")."; }}
  function selectRow(row) {{ rows.forEach((item)=>item.classList.remove("selected")); row.classList.add("selected"); let detail={{}}; try {{ detail=JSON.parse(row.dataset.detail||"{{}}"); }} catch(_err) {{}} ["name","status","vpnIp","location","channel","endpoint","handshake","rx","tx","total","share","today","all"].forEach((key)=>{{ const node=document.getElementById("detail-"+key); if(node) node.textContent=detail[key]||"—"; }}); const events=document.getElementById("detail-events"); if(events) {{ events.innerHTML=""; [detail.handshake+": состояние "+detail.status,"Трафик: "+detail.total+" / доля "+detail.share,"Площадка: "+detail.location,"Endpoint: "+detail.endpoint].forEach((text)=>{{ const li=document.createElement("li"); li.textContent=text; events.appendChild(li); }}); }} }}
  table.querySelectorAll(".sort-btn").forEach((button)=>button.addEventListener("click",()=>{{ const key=button.dataset.key; const type=button.dataset.type; const order=button.dataset.order==="desc"?"asc":"desc"; table.querySelectorAll(".sort-btn").forEach((item)=>item.removeAttribute("data-order")); button.dataset.order=order; rows.sort((left,right)=>{{ let a=left.getAttribute("data-sort-"+key)||""; let b=right.getAttribute("data-sort-"+key)||""; if(type==="number") {{ a=Number(a); b=Number(b); }} if(a<b) return order==="asc"?-1:1; if(a>b) return order==="asc"?1:-1; return 0; }}); rows.forEach((row)=>tbody.appendChild(row)); applyFilters(); }}));
  rows.forEach((row)=>row.addEventListener("click",()=>selectRow(row))); [search,statusFilter,locationFilter].forEach((node)=>node&&node.addEventListener("input",applyFilters)); if(periodFilter) periodFilter.addEventListener("change",applyPeriod); if(reset) reset.addEventListener("click",()=>{{ if(search) search.value=""; if(statusFilter) statusFilter.value="Статус: Все"; if(locationFilter) locationFilter.value="Все"; if(periodFilter) periodFilter.value="day"; applyPeriod(); applyFilters(); }}); applyPeriod(); applyFilters(); if(rows[0]) selectRow(rows[0]);
}})();
</script>
</body>
</html>"""


def write_reports(totals: Dict[str, object], daily: Dict[str, object], summary: Dict[str, object]) -> None:
    peers = summary.get("peers", [])
    bandwidth = summary.get("server", {}).get("bandwidth", {})
    capacity_bps = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    bandwidth_limit = format_rate(capacity_bps) if capacity_bps else "н/д"
    bandwidth_utilization = format_percent(bandwidth.get("utilization_percent"))
    bandwidth_daily = bandwidth.get("daily", {})
    bandwidth_day_average = format_rate(float(bandwidth_daily.get("average_current_total_bps", 0.0) or 0.0))
    bandwidth_day_peak = format_rate(float(bandwidth_daily.get("peak_current_total_bps", 0) or 0))
    bandwidth_day_peak_utilization = format_percent(bandwidth_daily.get("peak_utilization_percent"))
    bandwidth_state = describe_bandwidth_state(
        capacity_bps,
        bandwidth.get("utilization_percent"),
        int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0),
    )

    csv_path = REPORTS_DIR / "current_users.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "name",
                "endpoint_location",
                "vpn_ip",
                "is_active",
                "handshake_age_seconds",
                "current_rx_bps",
                "current_tx_bps",
                "current_total_bps",
                "current_share_percent",
                "daily_avg_total_bps",
                "today_rx_bytes",
                "today_tx_bytes",
                "today_bytes",
                "total_rx_bytes",
                "total_tx_bytes",
                "total_bytes",
                "last_handshake",
                "public_key",
            ]
        )
        for peer in peers:
            writer.writerow(
                [
                    peer["name"],
                    peer.get("endpoint_location", ""),
                    peer["vpn_ip"],
                    peer["is_active"],
                    peer["handshake_age_seconds"],
                    int(peer["current_rx_bps"]),
                    int(peer["current_tx_bps"]),
                    int(peer["current_total_bps"]),
                    round(float(peer.get("current_share_percent", 0.0)), 2),
                    int(peer["daily_avg_total_bps"]),
                    peer["today_rx_bytes"],
                    peer["today_tx_bytes"],
                    peer["today_bytes"],
                    peer["total_rx_bytes"],
                    peer["total_tx_bytes"],
                    peer["total_bytes"],
                    peer["last_handshake"],
                    peer["public_key"],
                ]
            )

    md_path = REPORTS_DIR / "current_users.md"
    lines = [
        "# Отчёт по Amnezia VPN",
        "",
        f"Обновлено: {summary.get('updated_at', '')}",
        f"Часовой пояс: {summary.get('timezone', TIMEZONE)}",
        f"Окно текущей скорости: {summary.get('vpn', {}).get('sample_window_seconds', 0)} сек.",
        f"Текущий суммарный поток: {format_rate(int(summary.get('vpn', {}).get('current_total_bps', 0) or 0))}",
        f"Средний поток за день: {bandwidth_day_average}",
        f"Пик потока за день: {bandwidth_day_peak}",
        f"Пиковая загрузка канала: {bandwidth_day_peak_utilization}",
        f"Лимит канала: {bandwidth_limit}",
        f"Загрузка канала: {bandwidth_utilization}",
        f"Состояние канала: {bandwidth_state['label']}",
        "",
        "| Имя | Локация | VPN IP | Активен | Handshake | Текущая | Доля потока | Средняя за день | Сегодня | Всего | Ключ |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for peer in peers:
        lines.append(
            "| {name} | {location} | {vpn_ip} | {active} | {handshake} | {current_total} | {current_share} | {daily_avg} | {today_total} | {total} | `{key}` |".format(
                name=peer["name"],
                location=peer.get("endpoint_location", ""),
                vpn_ip=peer["vpn_ip"],
                active="да" if peer["is_active"] else "нет",
                handshake=peer["handshake_age"],
                current_total=format_rate(peer["current_total_bps"]),
                current_share=format_percent(peer.get("current_share_percent")),
                daily_avg=format_rate(peer["daily_avg_total_bps"]),
                today_total=format_bytes(peer["today_bytes"]),
                total=format_bytes(peer["total_bytes"]),
                key=peer["public_key_short"],
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    save_json(WEB_SUMMARY_FILE, summary)
    WEB_INDEX_FILE.write_text(render_dashboard(summary), encoding="utf-8")


def build_peer_rows(
    peers: List[Dict[str, object]],
    totals: Dict[str, object],
    aliases: Dict[str, Dict[str, str]],
    state: Dict[str, object],
    meta: Dict[str, str],
    current_time: datetime,
    geo_cache: Optional[Dict[str, object]] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, object], Dict[str, object]]:
    current_ts = int(current_time.timestamp())
    same_container = state.get("container_id") == meta["container_id"]
    previous_sample_at = parse_iso_datetime(state.get("sampled_at"))
    sample_window_seconds = 0
    if same_container and previous_sample_at is not None:
        sample_window_seconds = max(int((current_time - previous_sample_at).total_seconds()), 0)

    current_date = current_time.date().isoformat()
    daily_path = DAILY_DIR / f"{current_date}.json"
    daily_data = load_json(daily_path, {"date": current_date, "timezone": TIMEZONE, "peers": {}})
    totals_data = totals
    day_elapsed_seconds = max(
        int((current_time - current_time.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()),
        1,
    )

    new_state = {
        "container_id": meta["container_id"],
        "started_at": meta["started_at"],
        "sampled_at": current_time.isoformat(),
        "peers": {},
    }

    if geo_cache is None:
        geo_cache = {"hosts": {}}

    rows: List[Dict[str, object]] = []
    for peer in peers:
        public_key = str(peer["public_key"])
        rx_bytes = int(peer["rx_bytes"])
        tx_bytes = int(peer["tx_bytes"])
        previous = state.get("peers", {}).get(public_key)
        counters_are_monotonic = (
            previous
            and same_container
            and rx_bytes >= int(previous.get("rx_bytes", 0))
            and tx_bytes >= int(previous.get("tx_bytes", 0))
        )

        if counters_are_monotonic:
            delta_rx = rx_bytes - int(previous["rx_bytes"])
            delta_tx = tx_bytes - int(previous["tx_bytes"])
        else:
            delta_rx = rx_bytes
            delta_tx = tx_bytes

        interval_seconds = sample_window_seconds if counters_are_monotonic and sample_window_seconds > 0 else 0
        current_rx_bps = (delta_rx / interval_seconds) if interval_seconds else 0.0
        current_tx_bps = (delta_tx / interval_seconds) if interval_seconds else 0.0
        alias = aliases.get(public_key, {})

        total_entry = totals_data["peers"].setdefault(
            public_key,
            {
                "public_key": public_key,
                "vpn_ip": peer["vpn_ip"],
                "allowed_ips": peer["allowed_ips"],
                "name": "",
                "total_rx_bytes": 0,
                "total_tx_bytes": 0,
                "first_seen_at": current_time.isoformat(),
                "last_seen_at": current_time.isoformat(),
                "last_endpoint": peer["endpoint"],
                "last_handshake": peer["latest_handshake"],
            },
        )
        total_entry["name"] = alias.get("name", total_entry.get("name", "")) or str(peer["vpn_ip"])
        total_entry["vpn_ip"] = peer["vpn_ip"]
        total_entry["allowed_ips"] = peer["allowed_ips"]
        total_entry["last_seen_at"] = current_time.isoformat()
        total_entry["last_endpoint"] = peer["endpoint"]
        total_entry["last_handshake"] = peer["latest_handshake"]
        total_entry["total_rx_bytes"] += delta_rx
        total_entry["total_tx_bytes"] += delta_tx

        daily_entry = daily_data["peers"].setdefault(
            public_key,
            {
                "public_key": public_key,
                "vpn_ip": peer["vpn_ip"],
                "name": alias.get("name", ""),
                "rx_bytes": 0,
                "tx_bytes": 0,
            },
        )
        daily_entry["vpn_ip"] = peer["vpn_ip"]
        daily_entry["name"] = alias.get("name", "")
        daily_entry["rx_bytes"] += delta_rx
        daily_entry["tx_bytes"] += delta_tx

        total_rx = int(total_entry.get("total_rx_bytes", 0))
        total_tx = int(total_entry.get("total_tx_bytes", 0))
        today_rx = int(daily_entry.get("rx_bytes", 0))
        today_tx = int(daily_entry.get("tx_bytes", 0))
        handshake_ts = int(peer["latest_handshake"])
        age_seconds = handshake_age_seconds(current_ts, handshake_ts)
        is_active = age_seconds is not None and age_seconds <= ACTIVE_WINDOW_SECONDS

        rows.append(
            {
                "name": total_entry["name"],
                "vpn_ip": peer["vpn_ip"],
                "endpoint": peer["endpoint"],
                "endpoint_location": format_peer_location(peer["endpoint"], geo_cache),
                "vpn_ip_sort": ip_sort_key(str(peer["vpn_ip"])),
                "public_key": public_key,
                "public_key_short": short_key(public_key),
                "last_handshake": format_handshake(handshake_ts),
                "handshake_age_seconds": age_seconds,
                "handshake_age": format_age(age_seconds),
                "is_active": is_active,
                "current_rx_bps": current_rx_bps,
                "current_tx_bps": current_tx_bps,
                "current_total_bps": current_rx_bps + current_tx_bps,
                "daily_avg_rx_bps": today_rx / day_elapsed_seconds,
                "daily_avg_tx_bps": today_tx / day_elapsed_seconds,
                "daily_avg_total_bps": (today_rx + today_tx) / day_elapsed_seconds,
                "today_rx_bytes": today_rx,
                "today_tx_bytes": today_tx,
                "today_bytes": today_rx + today_tx,
                "total_rx_bytes": total_rx,
                "total_tx_bytes": total_tx,
                "total_bytes": total_rx + total_tx,
            }
        )

        new_state["peers"][public_key] = {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "vpn_ip": peer["vpn_ip"],
            "allowed_ips": peer["allowed_ips"],
        }

    current_total_bps = sum(float(peer["current_total_bps"]) for peer in rows)
    for peer in rows:
        peer["current_share_percent"] = round((peer["current_total_bps"] / current_total_bps) * 100, 2) if current_total_bps else 0.0

    rows.sort(key=lambda item: (not item["is_active"], -item["current_total_bps"], -item["today_bytes"]))
    totals_data["updated_at"] = current_time.isoformat()
    return rows, daily_data, new_state


def collect() -> int:
    ensure_dirs()
    current_time = now_local()
    meta = inspect_container()
    interface_meta, peers = get_wg_dump()
    aliases = merge_aliases(peers)
    geo_cache = load_geo_cache()
    state = load_json(
        STATE_FILE,
        {"container_id": None, "started_at": None, "sampled_at": None, "peers": {}},
    )
    totals = load_json(
        TOTALS_FILE,
        {
            "timezone": TIMEZONE,
            "container": CONTAINER,
            "interface": INTERFACE,
            "updated_at": None,
            "peers": {},
        },
    )

    peer_rows, daily, new_state = build_peer_rows(
        peers=peers,
        totals=totals,
        aliases=aliases,
        state=state,
        meta=meta,
        current_time=current_time,
        geo_cache=geo_cache,
    )
    current_total_bps = int(sum(float(peer["current_total_bps"]) for peer in peer_rows))
    accounting_started_at = current_time
    first_seen_values = []
    for peer_info in totals.get("peers", {}).values():
        parsed = parse_iso_datetime(peer_info.get("first_seen_at"))
        if parsed is not None:
            first_seen_values.append(parsed)
    if first_seen_values:
        accounting_started_at = min(first_seen_values)
    day_started_at = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    server_info = load_server_info()
    server_status = get_server_status(current_time, server_info=server_info, current_total_bps=current_total_bps)
    daily_bandwidth = update_daily_bandwidth_stats(
        daily=daily,
        current_time=current_time,
        current_total_bps=current_total_bps,
        bandwidth=server_status.get("bandwidth", {}),
    )
    bandwidth_daily = {
        "sample_count": daily_bandwidth["sample_count"],
        "average_current_total_bps": daily_bandwidth["average_current_total_bps"],
        "peak_current_total_bps": daily_bandwidth["peak_current_total_bps"],
        "peak_utilization_percent": daily_bandwidth["peak_utilization_percent"],
        "peak_at": daily_bandwidth["peak_at"],
        "last_current_total_bps": daily_bandwidth["last_current_total_bps"],
        "last_utilization_percent": daily_bandwidth["last_utilization_percent"],
        "last_headroom_bytes_per_sec": daily_bandwidth["last_headroom_bytes_per_sec"],
    }
    server_status["bandwidth"]["daily"] = bandwidth_daily
    summary = {
        "updated_at": current_time.isoformat(),
        "timezone": TIMEZONE,
        "container": {
            "name": CONTAINER,
            "status": meta["status"],
            "started_at": meta["started_at"],
            "image": meta["image"],
        },
        "vpn": {
            "type": "AmneziaWG",
            "interface": INTERFACE,
            "listen_port": interface_meta.get("listen_port", 0),
            "total_peers": len(peer_rows),
            "active_connections": sum(1 for peer in peer_rows if peer["is_active"]),
            "current_rx_bps": int(sum(float(peer["current_rx_bps"]) for peer in peer_rows)),
            "current_tx_bps": int(sum(float(peer["current_tx_bps"]) for peer in peer_rows)),
            "current_total_bps": current_total_bps,
            "active_window_seconds": ACTIVE_WINDOW_SECONDS,
            "sample_window_seconds": max(
                int((current_time - parse_iso_datetime(state.get("sampled_at"))).total_seconds()),
                0,
            )
            if parse_iso_datetime(state.get("sampled_at")) is not None and state.get("container_id") == meta["container_id"]
            else 0,
        },
        "server": server_status,
        "server_info": server_info,
        "periods": {
            "current_day": {
                "started_at": day_started_at.isoformat(),
                "ended_at": current_time.isoformat(),
            },
            "accounting": {
                "started_at": accounting_started_at.isoformat(),
                "ended_at": current_time.isoformat(),
            },
        },
        "peers": peer_rows,
    }
    summary["warnings"] = build_warnings(summary)

    save_json(STATE_FILE, new_state)
    save_json(TOTALS_FILE, totals)
    if geo_cache.get("_dirty") or not GEO_CACHE_FILE.exists():
        geo_cache.pop("_dirty", None)
        save_json(GEO_CACHE_FILE, geo_cache)
    save_json(DAILY_DIR / f"{current_time.date().isoformat()}.json", daily)
    save_json(SUMMARY_FILE, summary)
    write_reports(totals, daily, summary)
    return 0


def build_default_summary(current_time: datetime, totals: Dict[str, object] | None = None) -> Dict[str, object]:
    totals = totals or {"updated_at": "", "timezone": TIMEZONE}
    return {
        "updated_at": totals.get("updated_at", current_time.isoformat()),
        "timezone": totals.get("timezone", TIMEZONE),
        "container": {"name": CONTAINER, "status": "unknown", "started_at": "", "image": ""},
        "vpn": {
            "type": "AmneziaWG",
            "interface": INTERFACE,
            "listen_port": 0,
            "total_peers": 0,
            "active_connections": 0,
            "current_rx_bps": 0,
            "current_tx_bps": 0,
            "current_total_bps": 0,
            "active_window_seconds": ACTIVE_WINDOW_SECONDS,
            "sample_window_seconds": 0,
        },
        "server": {
            "loadavg": {"1m": 0, "5m": 0, "15m": 0},
            "memory": {"used_bytes": 0, "used_percent": 0.0},
            "disk_root": {"used_bytes": 0, "used_percent": 0.0},
            "uptime_seconds": 0,
            "ping": {"target": PING_TARGET, "packet_loss_percent": None, "latency_avg_ms": None},
            "bandwidth": {
                "capacity_bytes_per_sec": 0,
                "capacity_mbps": 0.0,
                "source": "",
                "interface": "",
                "current_bytes_per_sec": 0,
                "utilization_percent": None,
                "headroom_bytes_per_sec": 0,
                "over_capacity_bytes_per_sec": 0,
                "daily": {
                    "sample_count": 0,
                    "average_current_total_bps": 0.0,
                    "peak_current_total_bps": 0,
                    "peak_utilization_percent": None,
                    "peak_at": None,
                    "last_current_total_bps": 0,
                    "last_utilization_percent": None,
                    "last_headroom_bytes_per_sec": 0,
                },
            },
        },
        "server_info": default_server_info(),
        "periods": {
            "current_day": {
                "started_at": current_time.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "ended_at": current_time.isoformat(),
            },
            "accounting": {
                "started_at": current_time.isoformat(),
                "ended_at": current_time.isoformat(),
            },
        },
        "peers": [],
        "warnings": [],
    }


def report() -> int:
    ensure_dirs()
    totals = load_json(TOTALS_FILE, {"peers": {}, "timezone": TIMEZONE, "updated_at": ""})
    current_time = now_local()
    current_date = current_time.date().isoformat()
    daily = load_json(DAILY_DIR / f"{current_date}.json", {"peers": {}})
    summary = load_json(SUMMARY_FILE, build_default_summary(current_time, totals))
    summary.setdefault(
        "periods",
        {
            "current_day": {
                "started_at": current_time.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "ended_at": summary.get("updated_at", current_time.isoformat()),
            },
            "accounting": {
                "started_at": summary.get("updated_at", current_time.isoformat()),
                "ended_at": summary.get("updated_at", current_time.isoformat()),
            },
        },
    )
    write_reports(totals, daily, summary)
    return 0


def render_status(summary: Dict[str, object]) -> List[str]:
    container = summary.get("container", {})
    vpn = summary.get("vpn", {})
    server = summary.get("server", {})
    warnings = summary.get("warnings", [])
    peers = summary.get("peers", [])
    updated_at = summary.get("updated_at", "н/д")
    bandwidth = server.get("bandwidth", {})
    bandwidth_capacity_bps = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    bandwidth_limit_label = format_rate(bandwidth_capacity_bps) if bandwidth_capacity_bps else "н/д"
    bandwidth_headroom_label = (
        format_rate(int(bandwidth.get("headroom_bytes_per_sec", 0) or 0)) if bandwidth_capacity_bps else "н/д"
    )
    bandwidth_daily = bandwidth.get("daily", {})
    bandwidth_day_average_label = format_rate(float(bandwidth_daily.get("average_current_total_bps", 0.0) or 0.0))
    bandwidth_day_peak_label = format_rate(float(bandwidth_daily.get("peak_current_total_bps", 0) or 0))
    bandwidth_state = describe_bandwidth_state(
        bandwidth_capacity_bps,
        bandwidth.get("utilization_percent"),
        int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0),
    )
    transport = server.get("transport", {})
    path_mtu_label = "н/д"
    if isinstance(transport, dict) and transport.get("estimated_path_mtu") is not None:
        path_mtu_label = f"{int(transport['estimated_path_mtu'])} B"
    interface_mtu_label = "н/д"
    if isinstance(transport, dict) and transport.get("interface_mtu") is not None:
        interface_mtu_label = f"{int(transport['interface_mtu'])} B"
    recommended_mtu_label = "н/д"
    if isinstance(transport, dict) and transport.get("recommended_interface_mtu") is not None:
        recommended_mtu_label = f"{int(transport['recommended_interface_mtu'])} B"
    lines = [
        f"Обновлено: {updated_at}",
        f"Контейнер: {container.get('name', '')} [{container.get('status', '')}] image={container.get('image', '')}",
        (
            "VPN: "
            f"{vpn.get('type', '')} / {vpn.get('interface', '')} "
            f"port={vpn.get('listen_port', 0)} "
            f"peers={vpn.get('total_peers', 0)} "
            f"active={vpn.get('active_connections', 0)} "
            f"window={vpn.get('sample_window_seconds', 0)}s "
            f"flow={format_rate(float(vpn.get('current_total_bps', 0)))}"
        ),
        (
            "Server: "
            f"load={server.get('loadavg', {}).get('1m', 0)} "
            f"memory={format_percent(server.get('memory', {}).get('used_percent', 0.0))} "
            f"disk={format_percent(server.get('disk_root', {}).get('used_percent', 0.0))} "
            f"uptime={format_age(int(server.get('uptime_seconds', 0)))}"
        ),
        (
            "Bandwidth: "
            f"current={format_rate(int(server.get('bandwidth', {}).get('current_bytes_per_sec', 0) or 0))} "
            f"avg_day={bandwidth_day_average_label} "
            f"peak_day={bandwidth_day_peak_label} "
            f"limit={bandwidth_limit_label} "
            f"util={format_percent(server.get('bandwidth', {}).get('utilization_percent'))} "
            f"headroom={bandwidth_headroom_label} "
            f"state={bandwidth_state['label']}"
        ),
        f"Transport: PMTU={path_mtu_label} awg0_mtu={interface_mtu_label} recommended={recommended_mtu_label}",
        f"Warnings: {len(warnings)}",
    ]
    if warnings:
        lines.append("Предупреждения:")
        lines.extend(f"- {item}" for item in warnings)
    if peers:
        lines.append("Top peers:")
        for peer in peers[:5]:
            lines.append(
                "- "
                f"{peer.get('name', '')} "
                f"{peer.get('vpn_ip', '')} "
                f"{format_rate(float(peer.get('current_total_bps', 0.0)))} "
                f"share={format_percent(peer.get('current_share_percent'))} "
                f"active={'да' if peer.get('is_active') else 'нет'}"
            )
    return lines


def status() -> int:
    ensure_dirs()
    current_time = now_local()
    totals = load_json(TOTALS_FILE, {"peers": {}, "timezone": TIMEZONE, "updated_at": ""})
    summary = load_json(SUMMARY_FILE, build_default_summary(current_time, totals))
    for line in render_status(summary):
        print(line)
    return 0


def doctor() -> int:
    ensure_dirs()
    script_root = Path(__file__).resolve().parent
    server_info_candidates = [
        script_root / "amnezia_server_info.json",
        script_root.parent / "amnezia_server_info.json",
    ]
    checks = [
        ("docker", shutil.which("docker") is not None, "docker not found in PATH"),
        ("ping", shutil.which("ping") is not None, "ping not found in PATH"),
        ("ip", shutil.which("ip") is not None, "ip not found in PATH"),
        (
            "server info template",
            any(path.exists() for path in server_info_candidates),
            "amnezia_server_info.json is missing",
        ),
        ("shell launcher", (script_root / "open_amnezia_dashboard.ps1").exists(), "open_amnezia_dashboard.ps1 is missing"),
        ("shell app", (script_root / "amnezia_vpn_shell.py").exists(), "amnezia_vpn_shell.py is missing"),
        ("dashboard server", (script_root / "amnezia_dashboard_server.py").exists(), "amnezia_dashboard_server.py is missing"),
        ("collector service", (script_root / "amnezia-traffic-collector.service").exists(), "amnezia-traffic-collector.service is missing"),
        ("dashboard service", (script_root / "amnezia-dashboard.service").exists(), "amnezia-dashboard.service is missing"),
        ("timer unit", (script_root / "amnezia-traffic-collector.timer").exists(), "amnezia-traffic-collector.timer is missing"),
    ]

    if SERVER_INFO_FILE.exists():
        try:
            load_server_info()
            runtime_server_info_ok = True
            runtime_server_info_error = ""
        except Exception as exc:  # pragma: no cover - defensive diagnostics
            runtime_server_info_ok = False
            runtime_server_info_error = f"server info load failed: {exc}"
    else:
        runtime_server_info_ok = True
        runtime_server_info_error = ""

    failure_count = 0
    for name, ok, failure_message in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"  {failure_message}")
            failure_count += 1

    if SERVER_INFO_FILE.exists():
        print(f"[{'OK' if runtime_server_info_ok else 'FAIL'}] runtime server info")
        if not runtime_server_info_ok:
            print(f"  {runtime_server_info_error}")
            failure_count += 1
    else:
        print(f"[WARN] runtime server info")
        print(f"  {SERVER_INFO_FILE} is missing; copy the template to the runtime data directory during deployment.")

    summary_exists = SUMMARY_FILE.exists()
    print(f"[{'OK' if summary_exists else 'WARN'}] summary cache")
    if not summary_exists:
        print("  Run `collect` first to create the cached dashboard state.")

    return 1 if failure_count else 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "collect"
    if command == "collect":
        return collect()
    if command == "report":
        return report()
    if command == "status":
        return status()
    if command == "doctor":
        return doctor()
    print(f"Unsupported command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
