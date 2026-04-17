#!/usr/bin/env python3
import csv
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


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
        "bandwidth": bandwidth,
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
    if vpn["active_connections"] == 0:
        warnings.append("Нет активных handshake в текущем окне активности.")
    bandwidth_utilization = bandwidth.get("utilization_percent")
    if bandwidth_utilization is not None and bandwidth_utilization >= 85:
        warnings.append(f"Канал загружен на {bandwidth_utilization:.2f}%.")
    over_capacity = int(bandwidth.get("over_capacity_bytes_per_sec", 0) or 0)
    if over_capacity > 0:
        warnings.append(f"Текущий поток выше лимита канала на {format_rate(over_capacity)}.")
    return warnings


def render_dashboard(summary: Dict[str, object]) -> str:
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
        rows.append(
            "<tr "
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
            f"<td>{esc(peer['name'])}</td>"
            f"<td>{esc(peer['vpn_ip'])}</td>"
            f"<td>{esc(peer['handshake_age'])}</td>"
            f"<td>{'да' if peer['is_active'] else 'нет'}</td>"
            f"<td>{esc(format_rate(peer['current_total_bps']))}</td>"
            f"<td>{esc(format_rate(peer['current_rx_bps']))}</td>"
            f"<td>{esc(format_rate(peer['current_tx_bps']))}</td>"
            f"<td>{esc(format_percent(peer['current_share_percent']))}</td>"
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
    bandwidth_source = bandwidth.get("source", "")
    bandwidth_interface = bandwidth.get("interface", "")
    bandwidth_limit_label = format_rate(bandwidth_capacity_bps) if bandwidth_capacity_bps else "н/д"
    bandwidth_utilization_label = format_percent(bandwidth_utilization)
    bandwidth_headroom_label = format_rate(bandwidth_headroom_bps) if bandwidth_capacity_bps else "н/д"
    bandwidth_context = bandwidth_source or bandwidth_interface or "auto"

    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="60">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Панель Amnezia VPN</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --fg: #111;
      --muted: #5f6258;
      --line: #d8dbd1;
      --ok: #175c2a;
      --warn: #8a3d13;
      --panel: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 20px;
      background: var(--bg);
      color: var(--fg);
      font: 14px/1.45 Consolas, "Liberation Mono", Menlo, monospace;
    }}
    h1, h2 {{ margin: 0 0 12px; font-size: 18px; }}
    h2 {{ margin-top: 24px; font-size: 15px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 12px;
    }}
    .status {{
      display: inline-block;
      padding: 4px 8px;
      border: 1px solid currentColor;
      font-weight: 700;
    }}
    .status.ok {{ color: var(--ok); }}
    .status.warn {{ color: var(--warn); }}
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
      background: #f0f2eb;
      position: sticky;
      top: 0;
    }}
    .muted {{ color: var(--muted); }}
    ul {{ margin: 0; padding-left: 18px; }}
    .table-wrap {{ overflow-x: auto; }}
    .hint {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    a {{ color: inherit; }}
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
      margin: 8px 0 12px;
    }}
    .view-btn {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: inherit;
      padding: 6px 10px;
      font: inherit;
      cursor: pointer;
    }}
    .view-btn.active {{
      border-color: var(--fg);
      font-weight: 700;
    }}
    .is-hidden {{
      display: none;
    }}
  </style>
</head>
<body>
  <h1>Панель Amnezia VPN</h1>
  <p class="muted">Обновлено: {updated_at} | Часовой пояс: {timezone} | Окно текущей скорости: {sample_window} сек.</p>
  <p><span class="status {status_class}">{status_label}</span></p>

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
      <strong>Пояснения</strong><br>
      Текущая скорость: среднее за последнее окно сбора.<br>
      Средняя за день: трафик с 00:00 / прошедшее время.<br>
      Сегодня: суммарный трафик за текущий день.<br>
      Всего: накопленный трафик с начала учёта.
    </div>
    <div class="panel">
      <strong>Периоды</strong><br>
      Текущие сутки: {day_range}<br>
      Весь период учёта: {accounting_range}
      <div class="hint">В таблице можно переключать режим просмотра по этим периодам.</div>
    </div>
    <div class="panel">
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
  <p class="muted">Клик по заголовку сортирует список.</p>
  <div class="view-switch">
    <button type="button" class="view-btn" data-mode="day">Текущие сутки</button>
    <button type="button" class="view-btn" data-mode="all">Весь период</button>
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

      applyMode("all");
    }})();
  </script>
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
        current_rx=esc(format_rate(current_rx_bps)),
        current_tx=esc(format_rate(current_tx_bps)),
        bandwidth_limit=esc(bandwidth_limit_label),
        bandwidth_utilization=esc(bandwidth_utilization_label),
        bandwidth_headroom=esc(bandwidth_headroom_label),
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
        rows="".join(rows) or "<tr><td colspan='11'>Пиры не найдены.</td></tr>",
    )


def write_reports(totals: Dict[str, object], daily: Dict[str, object], summary: Dict[str, object]) -> None:
    peers = summary.get("peers", [])
    bandwidth = summary.get("server", {}).get("bandwidth", {})
    capacity_bps = int(bandwidth.get("capacity_bytes_per_sec", 0) or 0)
    bandwidth_limit = format_rate(capacity_bps) if capacity_bps else "н/д"
    bandwidth_utilization = format_percent(bandwidth.get("utilization_percent"))

    csv_path = REPORTS_DIR / "current_users.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "name",
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
        f"Лимит канала: {bandwidth_limit}",
        f"Загрузка канала: {bandwidth_utilization}",
        "",
        "| Имя | VPN IP | Активен | Handshake | Текущая | Доля потока | Средняя за день | Сегодня | Всего | Ключ |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for peer in peers:
        lines.append(
            "| {name} | {vpn_ip} | {active} | {handshake} | {current_total} | {current_share} | {daily_avg} | {today_total} | {total} | `{key}` |".format(
                name=peer["name"],
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
            f"limit={bandwidth_limit_label} "
            f"util={format_percent(server.get('bandwidth', {}).get('utilization_percent'))} "
            f"headroom={bandwidth_headroom_label}"
        ),
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
        ("dashboard launcher", (script_root / "open_amnezia_dashboard.ps1").exists(), "open_amnezia_dashboard.ps1 is missing"),
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
