#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import queue
import shutil
import shlex
import sys
import threading
import time
import subprocess
import socket
import traceback
from datetime import datetime, timezone
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import ctypes

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover - Windows desktop dependency
    tk = None
    ttk = None


SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "scripts"):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

import amnezia_traffic_collector as collector  # noqa: E402


DEFAULT_REFRESH_SECONDS = 1.0
DEFAULT_LOCAL_PORT = 18765
DEFAULT_REMOTE_PORT = 18080
DEFAULT_HOST = "46.8.254.243"
DEFAULT_SSH_USER = "root"
REQUEST_TIMEOUT_SECONDS = 3.0
FETCH_RETRY_DELAYS_SECONDS = (0.2, 0.5)
_SINGLE_INSTANCE_MUTEX_NAME = "AutostopVPNShell"
_MAIN_WINDOW_TITLE_PREFIX = "Autostop VPN"
_ENDPOINT_LOCATION_CACHE: Dict[str, str] = {}
_SSH_CONNECT_TIMEOUT_SECONDS = 5
_SSH_TUNNEL_WAIT_SECONDS = 10.0
_SSH_SERVICE_TIMEOUT_SECONDS = 15.0
_DEBUG_LOG_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutostopVPN" / "shell_errors.log"
_SSH_LOG_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutostopVPN" / "ssh_tunnel.log"
HEADER_BASE_TEXT = "ssh tunnel // live vpn telemetry // ops cockpit"
APP_BG = "#070d13"
PANEL_BG = "#101b25"
PANEL_ALT_BG = "#132231"
BORDER_BG = "#263544"
TEXT_PRIMARY = "#dce8f4"
TEXT_MUTED = "#93a4b7"
ACCENT = "#32d06f"
ACCENT_SOFT = "#0f2d22"
ACCENT_2 = "#2f82ff"
CYAN = "#4cc9f0"
STATUS_BG = "#13202a"
STATUS_TEXT = "#b9f3ce"
WARN_BG = "#2a2515"
WARN_TEXT = "#f5c542"
ERROR_BG = "#2a1818"
ERROR_TEXT = "#ff5b55"
ROW_ACTIVE_BG = "#102a22"
ROW_INACTIVE_BG = "#0d1720"
ROW_SELECTED_BG = "#174a83"
INPUT_BG = "#0a131c"
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"
_REMOTE_MONITORING_START_SCRIPT = """
set -e
systemctl is-active --quiet amnezia-dashboard.service || systemctl start --no-block amnezia-dashboard.service
systemctl start amnezia-traffic-collector.timer
collector_state="$(systemctl show -p ActiveState --value amnezia-traffic-collector.service 2>/dev/null || true)"
[ "$collector_state" = "active" ] || [ "$collector_state" = "activating" ] || systemctl start --no-block amnezia-traffic-collector.service || true
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    curl -fsS --max-time 2 http://127.0.0.1:18080/dashboard.json >/dev/null && exit 0
    sleep 0.5
done
systemctl is-active --quiet amnezia-dashboard.service
""".strip()
_REMOTE_MONITORING_STOP_SCRIPT = """
set +e
systemctl stop amnezia-traffic-collector.timer
systemctl stop amnezia-traffic-collector.service
systemctl stop amnezia-dashboard.service
systemctl reset-failed amnezia-traffic-collector.service amnezia-dashboard.service
exit 0
""".strip()


def _state_badge_colors(state_class: str) -> tuple[str, str]:
    if state_class == "danger":
        return ERROR_BG, ERROR_TEXT
    if state_class == "warn":
        return "#2b200f", WARN_TEXT
    if state_class == "ok":
        return ACCENT_SOFT, ACCENT
    return "#141c23", "#a6b7c2"


def _append_debug_log(message: str) -> None:
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
    except Exception:  # pragma: no cover - logging should never break the UI
        pass


def _append_ssh_log(message: str) -> None:
    try:
        _SSH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SSH_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
    except Exception:  # pragma: no cover - logging should never break the UI
        pass


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_loadavg(loadavg: Dict[str, object]) -> str:
    one = _coerce_float(loadavg.get("1m"))
    five = _coerce_float(loadavg.get("5m"))
    fifteen = _coerce_float(loadavg.get("15m"))
    return f"{one:.2f} / {five:.2f} / {fifteen:.2f}"


def _format_latency(value: object) -> str:
    if value is None:
        return "н/д"
    return f"{_coerce_float(value):.2f} ms"


def _format_loss(value: object) -> str:
    if value is None:
        return "н/д"
    return collector.format_percent(_coerce_float(value))


def _format_storage(used_bytes: object, total_bytes: object, used_percent: object) -> str:
    used_label = collector.format_bytes(_coerce_int(used_bytes))
    total_label = collector.format_bytes(_coerce_int(total_bytes))
    percent_label = "н/д" if used_percent is None else f"{_coerce_float(used_percent):.2f}%"
    return f"{percent_label} ({used_label}/{total_label})"


def _format_percent_pair(part: int, total: int) -> str:
    if total <= 0:
        return "0 (0%)"
    return f"{part} ({(part / total) * 100:.1f}%)"


def _format_snapshot_age(updated_at: object) -> str:
    age_seconds = _snapshot_age_seconds(updated_at)
    if age_seconds is None:
        return "н/д"
    return collector.format_age(age_seconds)


def _snapshot_age_seconds(updated_at: object) -> Optional[int]:
    parsed = collector.parse_iso_datetime(str(updated_at)) if updated_at else None
    if parsed is None:
        return None
    return max(int((collector.now_local() - parsed).total_seconds()), 0)


def _format_snapshot_clock(updated_at: object) -> str:
    parsed = collector.parse_iso_datetime(str(updated_at)) if updated_at else None
    if parsed is None:
        return "н/д"
    return parsed.strftime("%H:%M:%S")


def _format_snapshot_day(updated_at: object) -> str:
    parsed = collector.parse_iso_datetime(str(updated_at)) if updated_at else None
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d")


def _format_sync_status_text(snapshot_label: str, age_label: str, refresh_seconds: float) -> str:
    return f"{HEADER_BASE_TEXT} • SYNC {snapshot_label} | AGE {age_label} | STEP {refresh_seconds:g}s"


def _split_endpoint(endpoint: object) -> str:
    value = str(endpoint or "").strip()
    if not value:
        return ""
    if value.startswith("[") and "]:" in value:
        return value[1 : value.index("]")]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _format_endpoint_location(endpoint: object) -> str:
    host = _split_endpoint(endpoint)
    if not host:
        return "нет endpoint"
    cached = _ENDPOINT_LOCATION_CACHE.get(host)
    if cached:
        return cached

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        label = host
    else:
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved:
            label = "локальная сеть"
        else:
            label = host

    _ENDPOINT_LOCATION_CACHE[host] = label
    return label


def _normalize_query(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def _location_bucket(value: object) -> str:
    label = str(value or "").strip()
    if not label:
        return "нет данных"
    return label.split(",", 1)[0].strip() or label


def _peer_matches_filter(
    peer: Dict[str, object],
    query: str,
    active_only: bool,
    status_filter: str = "Все",
    location_filter: str = "Все",
    quick_filter: str = "all",
) -> bool:
    is_active = bool(peer.get("active_value"))
    if not _peer_matches_quick_filter(peer, quick_filter):
        return False
    if active_only and not is_active:
        return False
    if status_filter == "Онлайн" and not is_active:
        return False
    if status_filter == "Оффлайн" and is_active:
        return False
    if location_filter not in ("", "Все"):
        if _location_bucket(peer.get("endpoint_location")) != location_filter:
            return False
    if not query:
        return True
    haystack = " ".join(
        [
            str(peer.get("name", "")),
            str(peer.get("vpn_ip", "")),
            str(peer.get("endpoint", "")),
            str(peer.get("endpoint_location", "")),
            str(peer.get("public_key_short", "")),
            str(peer.get("active", "")),
        ]
    )
    return query in _normalize_query(haystack)


def _peer_detail_note(peer: Dict[str, object]) -> str:
    if not peer:
        return "Выберите пир в таблице, чтобы увидеть детали."
    if peer.get("active_value"):
        if _coerce_float(peer.get("current_bps")) >= 1024 * 1024:
            return "Активный и заметно грузит канал."
        return "Активный пир, трафик идет прямо сейчас."
    age = peer.get("handshake_age_seconds")
    if age is None:
        return "Неактивный пир без свежего handshake."
    if _coerce_int(age) >= 900:
        return "Давно не выходил на связь."
    return "Пир пока неактивен, но недавно был виден."


def _peer_diagnostic_state(peer: Dict[str, object]) -> Dict[str, str]:
    if not peer:
        return {"key": "none", "label": "Нет выбора", "level": "neutral"}
    if not bool(peer.get("active_value")):
        return {"key": "offline", "label": "Нет связи", "level": "danger"}
    recommended_mtu = str(peer.get("recommended_mtu", "") or "")
    interface_mtu = str(peer.get("interface_mtu", "") or "")
    if recommended_mtu in {"", "н/д"}:
        return {"key": "mtu", "label": "MTU unknown", "level": "warn"}
    if interface_mtu and interface_mtu not in {"н/д", recommended_mtu}:
        return {"key": "mtu", "label": "MTU issue", "level": "danger"}
    age_seconds = peer.get("handshake_age_seconds")
    if age_seconds is not None and _coerce_int(age_seconds) >= 600:
        return {"key": "stale", "label": "Тихий >10м", "level": "warn"}
    if _coerce_float(peer.get("share_value")) >= 15 or _coerce_int(peer.get("current_bps")) >= 1024 * 1024:
        return {"key": "top", "label": "Топ трафика", "level": "ok"}
    return {"key": "ok", "label": "Норма", "level": "ok"}


def _peer_matches_quick_filter(peer: Dict[str, object], quick_filter: str) -> bool:
    if quick_filter in ("", "all", "Все"):
        return True
    diagnostic = _peer_diagnostic_state(peer)
    if quick_filter == "active":
        return bool(peer.get("active_value")) and _coerce_int(peer.get("current_bps")) > 0
    if quick_filter == "issues":
        return diagnostic["key"] in {"offline", "stale", "mtu"}
    return diagnostic["key"] == quick_filter


def _show_widget(widget: object) -> None:
    grid = getattr(widget, "grid", None)
    if callable(grid):
        grid()


def _hide_widget(widget: object) -> None:
    grid_remove = getattr(widget, "grid_remove", None)
    if callable(grid_remove):
        grid_remove()

def _test_local_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.7):
            return True
    except OSError:
        return False


def _acquire_single_instance_lock() -> Optional[ctypes.c_void_p]:
    if os.name != "nt":
        return None

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.CreateMutexW(None, True, _SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()

    last_error = kernel32.GetLastError()
    if last_error in (5, 183):  # ERROR_ACCESS_DENIED / ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None

    return ctypes.c_void_p(handle)


def _focus_existing_window() -> bool:
    if os.name != "nt":
        return False

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found = ctypes.c_bool(False)

    def _enum_callback(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if not title.startswith(_MAIN_WINDOW_TITLE_PREFIX):
            return True
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        found.value = True
        return False

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(_enum_callback)
    user32.EnumWindows(enum_proc, 0)
    return bool(found.value)


def _resolve_ssh_executable() -> str:
    for candidate in ("ssh.exe", "ssh"):
        command = shutil.which(candidate)
        if command:
            return command
    raise FileNotFoundError("ssh executable not found")


def _hidden_subprocess_kwargs() -> Dict[str, object]:
    kwargs: Dict[str, object] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _resolve_key_path(explicit_key_path: str = "") -> str:
    if explicit_key_path:
        candidate = Path(explicit_key_path)
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(f"SSH key not found: {candidate}")

    for env_name in ("AUTOSTOPVPN_SSH_KEY", "AUTOSTOPCRM_SSH_KEY"):
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            candidate = Path(env_value)
            if candidate.exists():
                return str(candidate)

    candidates = [
        Path.home() / ".ssh" / "autostopvpn_server_ed25519",
        Path.home() / ".ssh" / "autostopcrm_server_ed25519",
        Path.home() / ".ssh" / "codex_autostopvpn",
        Path.home() / ".ssh" / "codex_autostopcrm",
        Path.home() / ".ssh" / "codex_autostopcrm_key",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        "SSH key not found. Checked AUTOSTOPVPN_SSH_KEY, AUTOSTOPCRM_SSH_KEY, "
        "autostopvpn_server_ed25519, autostopcrm_server_ed25519, codex_autostopvpn, "
        "codex_autostopcrm, and codex_autostopcrm_key."
    )


def _run_ssh_remote_command(
    host: str,
    user: str,
    key_path: str,
    remote_command: str,
    timeout: float = _SSH_SERVICE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    ssh_executable = _resolve_ssh_executable()
    args = [
        ssh_executable,
        "-i",
        key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{user}@{host}",
        "bash",
        "-lc",
        shlex.quote(remote_command),
    ]
    _append_ssh_log("running remote command: " + " ".join(args[:-1]) + " <script>")
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **_hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        _append_ssh_log(f"remote command timed out after {timeout:g}s")
        raise TimeoutError(f"Remote monitoring command timed out after {timeout:g}s") from exc
    if completed.stdout.strip():
        _append_ssh_log("remote stdout: " + completed.stdout.strip())
    if completed.stderr.strip():
        _append_ssh_log("remote stderr: " + completed.stderr.strip())
    if completed.returncode != 0:
        raise RuntimeError(f"Remote monitoring command failed with code {completed.returncode}: {completed.stderr.strip()}")
    return completed


def _run_remote_monitoring_control(host: str, user: str, key_path: str, action: str) -> None:
    if action == "start":
        _run_ssh_remote_command(host, user, key_path, _REMOTE_MONITORING_START_SCRIPT)
        return
    if action == "stop":
        _run_ssh_remote_command(host, user, key_path, _REMOTE_MONITORING_STOP_SCRIPT)
        return
    raise ValueError(f"Unknown remote monitoring action: {action}")


def _start_ssh_tunnel(host: str, user: str, key_path: str, local_port: int, remote_port: int) -> subprocess.Popen[str]:
    ssh_executable = _resolve_ssh_executable()
    tunnel_spec = f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"
    args = [
            ssh_executable,
            "-i",
            key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_SECONDS}",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-N",
            "-L",
            tunnel_spec,
            f"{user}@{host}",
        ]
    _append_ssh_log("starting tunnel: " + " ".join(args))
    log_handle = _SSH_LOG_PATH.open("a", encoding="utf-8")
    log_handle.write(f"{datetime.now(timezone.utc).isoformat()} command started\n")
    log_handle.flush()
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        **_hidden_subprocess_kwargs(),
    )


_TRANSIENT_FETCH_EXCEPTIONS = (ConnectionResetError, TimeoutError, RemoteDisconnected)


def _is_transient_fetch_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return False
    if isinstance(exc, _TRANSIENT_FETCH_EXCEPTIONS):
        return True
    if isinstance(exc, URLError):
        return isinstance(exc.reason, _TRANSIENT_FETCH_EXCEPTIONS)
    return False


def fetch_summary(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> Dict[str, object]:
    request = Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    retry_delays = FETCH_RETRY_DELAYS_SECONDS
    for attempt in range(len(retry_delays) + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            break
        except Exception as exc:
            if attempt >= len(retry_delays) or not _is_transient_fetch_error(exc):
                raise
            time.sleep(retry_delays[attempt])
    summary = json.loads(payload)
    if not isinstance(summary, dict):
        raise ValueError("Dashboard payload must be a JSON object")
    return summary


def build_view_model(summary: Dict[str, object], source_url: str, refresh_seconds: float) -> Dict[str, object]:
    container = summary.get("container", {}) if isinstance(summary.get("container", {}), dict) else {}
    vpn = summary.get("vpn", {}) if isinstance(summary.get("vpn", {}), dict) else {}
    server = summary.get("server", {}) if isinstance(summary.get("server", {}), dict) else {}
    bandwidth = server.get("bandwidth", {}) if isinstance(server.get("bandwidth", {}), dict) else {}
    transport = server.get("transport", {}) if isinstance(server.get("transport", {}), dict) else {}
    daily = bandwidth.get("daily", {}) if isinstance(bandwidth.get("daily", {}), dict) else {}
    loadavg = server.get("loadavg", {}) if isinstance(server.get("loadavg", {}), dict) else {}
    memory = server.get("memory", {}) if isinstance(server.get("memory", {}), dict) else {}
    disk = server.get("disk_root", {}) if isinstance(server.get("disk_root", {}), dict) else {}
    ping = server.get("ping", {}) if isinstance(server.get("ping", {}), dict) else {}
    warnings = [str(item) for item in summary.get("warnings", []) if str(item).strip()]
    server_ping_label = _format_latency(ping.get("latency_avg_ms"))
    server_packet_loss_label = _format_loss(ping.get("packet_loss_percent"))
    snapshot_age_seconds = _snapshot_age_seconds(summary.get("updated_at"))

    capacity_bps = _coerce_int(bandwidth.get("capacity_bytes_per_sec"))
    current_total_bps = _coerce_int(vpn.get("current_total_bps"))
    current_rx_bps = _coerce_int(vpn.get("current_rx_bps"))
    current_tx_bps = _coerce_int(vpn.get("current_tx_bps"))
    utilization_percent = bandwidth.get("utilization_percent")
    estimated_path_mtu = _coerce_int(transport.get("estimated_path_mtu"))
    path_mtu_label = f"{estimated_path_mtu} B" if estimated_path_mtu else "н/д"
    path_mtu_note = "DF ping probe" if transport else "н/д"
    interface_mtu = _coerce_int(transport.get("interface_mtu"))
    interface_mtu_label = f"{interface_mtu} B" if interface_mtu else "н/д"
    recommended_mtu = _coerce_int(transport.get("recommended_interface_mtu"))
    recommended_mtu_label = f"{recommended_mtu} B" if recommended_mtu else "н/д"
    bandwidth_state = collector.describe_bandwidth_state(
        capacity_bps,
        _coerce_float(utilization_percent) if utilization_percent is not None else None,
        _coerce_int(bandwidth.get("over_capacity_bytes_per_sec")),
    )

    peer_rows: List[Dict[str, object]] = []
    for peer in summary.get("peers", []):
        if not isinstance(peer, dict):
            continue
        current_bps = _coerce_int(peer.get("current_total_bps"))
        total_bytes = _coerce_int(peer.get("total_bytes"))
        endpoint = str(peer.get("endpoint", "") or "")
        endpoint_location = str(peer.get("endpoint_location") or "")
        if not endpoint_location:
            endpoint_location = _format_endpoint_location(endpoint)
        peer_rows.append(
            {
                "name": str(peer.get("name", "")),
                "vpn_ip": str(peer.get("vpn_ip", "")),
                "channel": str(vpn.get("interface", "") or "awg0"),
                "endpoint": endpoint,
                "endpoint_location": endpoint_location,
                "location_bucket": _location_bucket(endpoint_location),
                "public_key": str(peer.get("public_key", "")),
                "public_key_short": str(peer.get("public_key_short", "")),
                "active": "активен" if bool(peer.get("is_active")) else "нет связи",
                "active_value": bool(peer.get("is_active")),
                "handshake_age_seconds": peer.get("handshake_age_seconds"),
                "handshake": collector.format_age(peer.get("handshake_age_seconds")),
                "current": collector.format_rate(current_bps),
                "current_bps": current_bps,
                "rx": collector.format_rate(_coerce_int(peer.get("current_rx_bps"))),
                "rx_bps": _coerce_int(peer.get("current_rx_bps")),
                "tx": collector.format_rate(_coerce_int(peer.get("current_tx_bps"))),
                "tx_bps": _coerce_int(peer.get("current_tx_bps")),
                "share": collector.format_percent(peer.get("current_share_percent")),
                "share_value": _coerce_float(peer.get("current_share_percent")),
                "today": collector.format_bytes(_coerce_int(peer.get("today_bytes"))),
                "today_bytes": _coerce_int(peer.get("today_bytes")),
                "total": collector.format_bytes(total_bytes),
                "total_bytes": total_bytes,
                "path_mtu": path_mtu_label,
                "path_mtu_note": path_mtu_note,
                "interface_mtu": interface_mtu_label,
                "interface_mtu_note": "awg0 inside container" if interface_mtu else "н/д",
                "recommended_mtu": recommended_mtu_label,
                "server_ping": server_ping_label,
                "packet_loss": server_packet_loss_label,
            }
        )

    peer_rows.sort(
        key=lambda item: (
            not bool(item["active_value"]),
            -_coerce_int(item["current_bps"]),
            -_coerce_int(item["total_bytes"]),
            str(item["name"]).lower(),
        )
    )

    bandwidth_limit = collector.format_rate(capacity_bps) if capacity_bps else "н/д"
    bandwidth_headroom = (
        collector.format_rate(_coerce_int(bandwidth.get("headroom_bytes_per_sec"))) if capacity_bps else "н/д"
    )
    bandwidth_utilization = collector.format_percent(utilization_percent if utilization_percent is not None else None)
    offline_connections = max(_coerce_int(vpn.get("total_peers")) - _coerce_int(vpn.get("active_connections")), 0)
    return {
        "source_url": source_url,
        "refresh_seconds": max(float(refresh_seconds), 0.5),
        "updated_label": collector.format_timestamp(summary.get("updated_at")),
        "updated_clock": _format_snapshot_clock(summary.get("updated_at")),
        "updated_day": _format_snapshot_day(summary.get("updated_at")),
        "age_label": _format_snapshot_age(summary.get("updated_at")),
        "snapshot_age_seconds": snapshot_age_seconds,
        "container_label": f"{container.get('name', '')} [{collector.translate_container_status(str(container.get('status', '')))}]",
        "container_status": collector.translate_container_status(str(container.get("status", ""))),
        "container_image": str(container.get("image", "")),
        "vpn_label": f"{vpn.get('type', 'VPN')} / {vpn.get('interface', '')}",
        "vpn_type": str(vpn.get("type", "VPN")),
        "vpn_interface": str(vpn.get("interface", "")),
        "listen_port": _coerce_int(vpn.get("listen_port")),
        "total_peers": _coerce_int(vpn.get("total_peers")),
        "active_connections": _coerce_int(vpn.get("active_connections")),
        "offline_connections": offline_connections,
        "active_connections_label": _format_percent_pair(_coerce_int(vpn.get("active_connections")), _coerce_int(vpn.get("total_peers"))),
        "offline_connections_label": _format_percent_pair(offline_connections, _coerce_int(vpn.get("total_peers"))),
        "current_rx": collector.format_rate(_coerce_int(vpn.get("current_rx_bps"))),
        "current_tx": collector.format_rate(_coerce_int(vpn.get("current_tx_bps"))),
        "current_total": collector.format_rate(current_total_bps),
        "current_rx_bps": current_rx_bps,
        "current_tx_bps": current_tx_bps,
        "current_total_bps": current_total_bps,
        "bandwidth_limit_bps": capacity_bps,
        "bandwidth_utilization_value": _coerce_float(utilization_percent) if utilization_percent is not None else 0.0,
        "bandwidth_limit": bandwidth_limit,
        "bandwidth_utilization": bandwidth_utilization,
        "bandwidth_headroom": bandwidth_headroom,
        "bandwidth_state_class": bandwidth_state["class"],
        "bandwidth_state_label": bandwidth_state["label"],
        "bandwidth_state_note": bandwidth_state["note"],
        "bandwidth_bar_width_percent": float(bandwidth_state["bar_width_percent"]),
        "path_mtu": path_mtu_label,
        "path_mtu_note": path_mtu_note,
        "interface_mtu": interface_mtu_label,
        "interface_mtu_note": "awg0 inside container" if interface_mtu else "н/д",
        "recommended_mtu": recommended_mtu_label,
        "daily_average": collector.format_rate(_coerce_float(daily.get("average_current_total_bps"))),
        "daily_peak": collector.format_rate(_coerce_float(daily.get("peak_current_total_bps"))),
        "daily_peak_utilization": collector.format_percent(daily.get("peak_utilization_percent")),
        "server_load": _format_loadavg(loadavg),
        "server_memory": _format_storage(
            memory.get("used_bytes"),
            memory.get("total_bytes"),
            memory.get("used_percent"),
        ),
        "server_disk": _format_storage(
            disk.get("used_bytes"),
            disk.get("total_bytes"),
            disk.get("used_percent"),
        ),
        "server_uptime": collector.format_age(_coerce_int(server.get("uptime_seconds"))),
        "server_ping": server_ping_label,
        "server_packet_loss": server_packet_loss_label,
        "server_packet_loss_value": _coerce_float(ping.get("packet_loss_percent")) if ping.get("packet_loss_percent") is not None else 0.0,
        "warnings": warnings,
        "peer_rows": peer_rows,
    }


class ShellApp:
    def __init__(
        self,
        root: tk.Tk,
        host: str,
        ssh_user: str,
        key_path: str,
        local_port: int,
        remote_port: int,
        refresh_seconds: float,
        dashboard_url: Optional[str] = None,
        manage_remote_monitoring: bool = True,
    ) -> None:
        self.root = root
        self.host = host
        self.ssh_user = ssh_user
        self.key_path = key_path
        self.local_port = local_port
        self.remote_port = remote_port
        self.dashboard_url = dashboard_url or f"http://127.0.0.1:{local_port}/dashboard.json"
        self.manage_remote_monitoring = manage_remote_monitoring
        self.refresh_seconds = max(float(refresh_seconds), 0.5)
        self.refresh_ms = max(int(self.refresh_seconds * 1000), 500)
        self._refresh_after_id: Optional[str] = None
        self._refresh_result_after_id: Optional[str] = None
        self._refresh_in_flight = False
        self._closed = False
        self._remote_monitoring_started = False
        self._last_model: Optional[Dict[str, object]] = None
        self._last_snapshot_label: Optional[str] = None
        self._ssh_process: Optional[subprocess.Popen[bytes]] = None
        self._refresh_results: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._traffic_history: List[float] = []
        self._traffic_history_limit = 90
        self._all_peer_rows: List[Dict[str, object]] = []
        self._filtered_peer_rows: List[Dict[str, object]] = []
        self._peer_rows_by_key: Dict[str, Dict[str, object]] = {}
        self._selected_peer_key: Optional[str] = None
        self._search_query = tk.StringVar(master=self.root, value="")
        self._active_only = tk.BooleanVar(master=self.root, value=False)
        self._status_filter = tk.StringVar(master=self.root, value="Все")
        self._location_filter = tk.StringVar(master=self.root, value="Все")
        self._period_filter = tk.StringVar(master=self.root, value="Сегодня")
        self._quick_filter = "all"
        self._quick_filter_buttons: Dict[str, tk.Button] = {}
        self._location_filter_combo: Optional[ttk.Combobox] = None
        self._top_status_labels: Dict[str, tk.Label] = {}
        self._ops_summary_label: Optional[tk.Label] = None
        self._channel_row_value_labels: Dict[str, tk.Label] = {}
        self._card_value_labels: Dict[str, tk.Label] = {}
        self._card_secondary_value_labels: Dict[str, tk.Label] = {}
        self._card_note_labels: Dict[str, tk.Label] = {}
        self._detail_value_labels: Dict[str, tk.Label] = {}
        self._detail_event_labels: List[tk.Label] = []
        self._detail_header_label: Optional[tk.Label] = None
        self._detail_subtitle_label: Optional[tk.Label] = None
        self._detail_note_label: Optional[tk.Label] = None
        self._trend_canvas: Optional[tk.Canvas] = None
        self._trend_value_label: Optional[tk.Label] = None
        self._trend_stat_labels: Dict[str, tk.Label] = {}
        self._channel_meter_canvas: Optional[tk.Canvas] = None
        self._trend_phase = 0
        self._last_trend_signature: Optional[tuple] = None
        self._status_base_text = HEADER_BASE_TEXT
        self._status_indicator_canvas: Optional[tk.Canvas] = None
        self._status_indicator_dot: Optional[int] = None
        self._status_blink_job: Optional[str] = None
        self._status_blink_on = False
        self._status_mode = "offline"
        self._suppress_tree_select_event = False

        self.root.title("Autostop VPN Monitor")
        self.root.geometry("1660x940")
        self.root.minsize(1280, 760)
        try:
            self.root.state("zoomed")
        except tk.TclError:  # pragma: no cover - platform/window-manager dependent
            pass
        self.root.configure(background=APP_BG)

        self._build_styles()
        self._build_layout()
        self._schedule_status_indicator_tick()
        self._search_query.trace_add("write", lambda *_args: self._apply_peer_filter())
        self._active_only.trace_add("write", lambda *_args: self._apply_peer_filter())
        self._status_filter.trace_add("write", lambda *_args: self._apply_peer_filter())
        self._location_filter.trace_add("write", lambda *_args: self._apply_peer_filter())
        self._period_filter.trace_add("write", lambda *_args: self._apply_peer_filter())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<F5>", self._on_refresh_key)
        self.root.bind_all("<Control-r>", self._on_refresh_key)
        self.root.bind_all("<Control-R>", self._on_refresh_key)
        self.root.report_callback_exception = self._report_callback_exception

        self._schedule_refresh_result_poll()
        self.root.after(50, self.request_refresh)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - theme availability varies
            pass
        style.configure("Shell.TFrame", background=APP_BG)
        style.configure("Shell.TLabel", background=APP_BG, foreground=TEXT_PRIMARY, font=(FONT_UI, 10))
        style.configure("ShellTitle.TLabel", background=PANEL_ALT_BG, foreground=TEXT_PRIMARY, font=(FONT_UI, 19, "bold"))
        style.configure("ShellSection.TLabelframe", background=PANEL_BG, foreground=TEXT_PRIMARY)
        style.configure(
            "ShellSection.TLabelframe.Label",
            background=PANEL_BG,
            foreground=TEXT_PRIMARY,
            font=(FONT_UI, 9, "bold"),
        )
        style.configure("ShellValue.TLabel", background=PANEL_BG, foreground=TEXT_PRIMARY, font=(FONT_UI, 11, "bold"))
        style.configure("ShellSmall.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=(FONT_UI, 9))
        style.configure(
            "Shell.Treeview",
            font=(FONT_MONO, 9),
            rowheight=30,
            background=PANEL_BG,
            fieldbackground=PANEL_BG,
            foreground=TEXT_PRIMARY,
            borderwidth=0,
        )
        style.configure(
            "Shell.Treeview.Heading",
            font=(FONT_UI, 9, "bold"),
            background=PANEL_ALT_BG,
            foreground=TEXT_PRIMARY,
            relief="flat",
        )
        style.configure(
            "Shell.Vertical.TScrollbar",
            background=PANEL_ALT_BG,
            troughcolor=INPUT_BG,
            bordercolor=BORDER_BG,
            arrowcolor=TEXT_MUTED,
            relief="flat",
        )
        style.map(
            "Shell.Treeview",
            background=[("selected", ROW_SELECTED_BG)],
            foreground=[("selected", TEXT_PRIMARY)],
        )

    def _build_layout(self) -> None:
        root = self.root
        root.configure(background=APP_BG)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        container = tk.Frame(root, bg=APP_BG, padx=16, pady=14)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        hero = tk.Frame(container, bg=APP_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=12, pady=7)
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=0)
        tk.Label(hero, text="VPN Мониторинг", bg=APP_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 16, "normal")).grid(
            row=0, column=0, sticky="w"
        )
        self._ops_summary_label = tk.Label(
            hero,
            text="ожидание snapshot...",
            bg=APP_BG,
            fg=TEXT_PRIMARY,
            font=(FONT_UI, 9, "bold"),
            wraplength=820,
            justify="left",
            anchor="w",
        )
        self._ops_summary_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.updated_label = tk.Label(
            hero,
            text=f"{HEADER_BASE_TEXT} • waiting for snapshot",
            bg=APP_BG,
            fg=TEXT_MUTED,
            font=(FONT_MONO, 8),
            wraplength=760,
            justify="left",
            anchor="w",
        )
        self.updated_label.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        status_panel = tk.Frame(hero, bg=APP_BG)
        status_panel.grid(row=0, column=1, rowspan=3, sticky="ne", padx=(12, 0))
        status_top = tk.Frame(status_panel, bg=APP_BG)
        status_top.grid(row=0, column=0, sticky="e")
        self._status_indicator_canvas = tk.Canvas(
            status_top,
            width=16,
            height=16,
            bg=APP_BG,
            highlightthickness=0,
            bd=0,
        )
        self._status_indicator_canvas.pack(side="left", padx=(0, 6))
        self._status_indicator_dot = self._status_indicator_canvas.create_oval(3, 3, 13, 13, fill=ACCENT, outline=ACCENT)
        self.state_label = tk.Label(
            status_top,
            text="Система: ожидание",
            bg=APP_BG,
            fg=ACCENT,
            font=(FONT_UI, 9, "bold"),
        )
        self.state_label.pack(side="left")
        for key, label in (
            ("vpn", "Сервер VPN: —"),
            ("loss", "Потери: —"),
            ("time", "Время: —"),
            ("uptime", "Аптайм: —"),
            ("refresh", f"Обновление: {self.refresh_seconds:g} сек"),
        ):
            item = tk.Label(status_top, text=label, bg=APP_BG, fg=TEXT_MUTED, font=(FONT_UI, 9))
            item.pack(side="left", padx=(18, 0))
            self._top_status_labels[key] = item
        status_actions = tk.Frame(status_panel, bg=APP_BG)
        status_actions.grid(row=1, column=0, sticky="e", pady=(5, 0))
        tk.Button(
            status_actions,
            text="Обновить",
            command=self.request_refresh,
            bg=INPUT_BG,
            fg=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 9, "bold"),
            padx=10,
            pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            status_actions,
            text="Закрыть мониторинг",
            command=self.close,
            bg=INPUT_BG,
            fg=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            activebackground=ERROR_BG,
            activeforeground=ERROR_TEXT,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ERROR_TEXT,
            font=(FONT_UI, 9, "bold"),
            padx=10,
            pady=4,
        ).pack(side="left")

        overview = tk.Frame(container, bg=APP_BG)
        overview.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        overview.columnconfigure(0, weight=2, minsize=420)
        overview.columnconfigure(1, weight=5)
        overview.rowconfigure(1, weight=1)

        channel_panel = tk.Frame(overview, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=14, pady=12)
        channel_panel.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        channel_panel.columnconfigure(0, weight=1)
        tk.Label(channel_panel, text="Использование канала", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            channel_panel,
            text="Основной канал: awg0 / autodetected",
            bg=PANEL_BG,
            fg=TEXT_MUTED,
            font=(FONT_UI, 9),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        channel_value_row = tk.Frame(channel_panel, bg=PANEL_BG)
        channel_value_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        channel_value_row.columnconfigure(0, weight=1)
        channel_value = tk.Label(channel_value_row, text="—", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_MONO, 20, "bold"))
        channel_value.grid(row=0, column=0, sticky="w")
        channel_percent = tk.Label(channel_value_row, text="—", bg=PANEL_BG, fg=ACCENT, font=(FONT_MONO, 18, "bold"))
        channel_percent.grid(row=0, column=1, sticky="e")
        meter = tk.Canvas(channel_panel, height=13, bg=PANEL_BG, highlightthickness=0, bd=0)
        meter.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 10))
        self._channel_meter_canvas = meter  # type: ignore[attr-defined]
        self._card_value_labels["channel"] = channel_value
        self._card_secondary_value_labels["channel"] = channel_percent
        channel_rows = [
            ("rx", "Входящий", ACCENT_2),
            ("tx", "Исходящий", ACCENT),
            ("peak", "Пик за сегодня", WARN_TEXT),
            ("average", "Средний за сегодня", TEXT_MUTED),
            ("limit", "Лимит канала", TEXT_MUTED),
            ("headroom", "Запас канала", ACCENT),
        ]
        for index, (key, title, color) in enumerate(channel_rows, start=4):
            tk.Label(channel_panel, text=title, bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 9)).grid(
                row=index, column=0, sticky="w", pady=3
            )
            value = tk.Label(channel_panel, text="—", bg=PANEL_BG, fg=color, font=(FONT_MONO, 9, "bold"), anchor="e")
            value.grid(row=index, column=1, sticky="e", pady=3)
            self._channel_row_value_labels[key] = value

        cards = tk.Frame(overview, bg=APP_BG)
        cards.grid(row=0, column=1, sticky="nsew")
        cards.rowconfigure(0, weight=1)
        for idx in range(4):
            cards.columnconfigure(idx, weight=1, uniform="cards")
        self._card_frames: Dict[str, tk.Frame] = {}
        card_specs = [
            ("peers", "Пиры", ACCENT_2),
            ("server", "VPN-сервер", "#82d98d"),
            ("snapshot", "Снимки конфигурации", "#a8b3ff"),
            ("leader", "Лидер трафика", "#f2c46d"),
        ]
        for idx, spec in enumerate(card_specs):
            if len(spec) == 4:
                key, title, accent, show_secondary = spec
            else:
                key, title, accent = spec
                show_secondary = False
            card, value_label, secondary_value_label, note_label = self._create_metric_card(
                cards,
                title,
                accent,
                show_secondary_value=show_secondary,
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 8, 0))
            self._card_frames[key] = card
            self._card_value_labels[key] = value_label
            if secondary_value_label is not None:
                self._card_secondary_value_labels[key] = secondary_value_label
            self._card_note_labels[key] = note_label

        trend_card = tk.Frame(overview, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=12, pady=8)
        trend_card.grid(row=1, column=1, sticky="nsew", pady=(8, 0))
        trend_card.columnconfigure(0, weight=1)
        trend_card.columnconfigure(1, weight=0)
        trend_header = tk.Frame(trend_card, bg=PANEL_BG)
        trend_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        trend_header.columnconfigure(0, weight=1)
        tk.Label(trend_header, text="Живой трафик", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._trend_value_label = tk.Label(trend_header, text="—", bg=PANEL_BG, fg=ACCENT, font=(FONT_MONO, 9, "bold"))
        self._trend_value_label.grid(row=0, column=1, sticky="e")
        self._trend_canvas = tk.Canvas(
            trend_card,
            height=148,
            bg=INPUT_BG,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            bd=0,
        )
        self._trend_canvas.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 14))
        trend_stats = tk.Frame(trend_card, bg=PANEL_BG)
        trend_stats.grid(row=1, column=1, sticky="nsew", pady=(6, 0))
        tk.Label(trend_stats, text="Текущие значения", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        for row_index, (key, title, color) in enumerate(
            (
                ("rx", "Входящий", ACCENT_2),
                ("tx", "Исходящий", ACCENT),
                ("total", "Всего", CYAN),
                ("peak", "Пиковая сегодня", WARN_TEXT),
                ("average", "Средняя сегодня", TEXT_MUTED),
                ("utilization", "Загрузка канала", ACCENT),
            ),
            start=1,
        ):
            tk.Label(trend_stats, text=title, bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 8)).grid(
                row=row_index, column=0, sticky="w", pady=3
            )
            value = tk.Label(trend_stats, text="—", bg=PANEL_BG, fg=color, font=(FONT_MONO, 8, "bold"), anchor="e")
            value.grid(row=row_index, column=1, sticky="e", pady=3, padx=(18, 0))
            self._trend_stat_labels[key] = value

        quick_bar = tk.Frame(container, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=10, pady=5)
        quick_bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for idx, (key, title) in enumerate(
            (
                ("all", "Все пиры"),
                ("top", "Топ трафика"),
                ("offline", "Без связи"),
                ("stale", "Тихие >10м"),
                ("mtu", "MTU issue"),
                ("active", "Активные сейчас"),
                ("issues", "Требуют внимания"),
            )
        ):
            button = tk.Button(
                quick_bar,
                text=title,
                command=lambda value=key: self._set_quick_filter(value),
                bg=ACCENT_SOFT if key == "all" else PANEL_ALT_BG,
                fg=ACCENT if key == "all" else TEXT_MUTED,
                relief="flat",
                bd=0,
                activebackground=ACCENT_SOFT,
                activeforeground=ACCENT,
                highlightthickness=1,
                highlightbackground=ACCENT if key == "all" else BORDER_BG,
                highlightcolor=ACCENT,
                font=(FONT_UI, 8, "bold"),
                padx=8,
                pady=3,
            )
            button.grid(row=0, column=idx, sticky="w", padx=(0 if idx == 0 else 6, 0))
            self._quick_filter_buttons[key] = button

        filter_bar = tk.Frame(container, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=10, pady=6)
        filter_bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        filter_bar.columnconfigure(0, weight=1)
        search_entry = tk.Entry(
            filter_bar,
            textvariable=self._search_query,
            relief="flat",
            bd=0,
            bg=INPUT_BG,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 10),
        )
        search_entry.grid(row=0, column=0, sticky="ew")
        search_entry.insert(0, "")
        search_entry.configure(font=(FONT_UI, 9))

        def _combo(column: int, label: str, variable: tk.StringVar, values: List[str], width: int) -> ttk.Combobox:
            holder = tk.Frame(filter_bar, bg=PANEL_BG)
            holder.grid(row=0, column=column, sticky="e", padx=(10, 0))
            tk.Label(holder, text=label, bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 8, "bold")).pack(side="left", padx=(0, 5))
            combo = ttk.Combobox(holder, textvariable=variable, values=values, state="readonly", width=width)
            combo.pack(side="left")
            return combo

        _combo(1, "Статус:", self._status_filter, ["Все", "Онлайн", "Оффлайн"], 9)
        _combo(2, "Период:", self._period_filter, ["Сегодня", "Всего"], 9)
        self._location_filter_combo = _combo(3, "Площадка:", self._location_filter, ["Все"], 16)
        tk.Button(
            filter_bar,
            text="Сбросить",
            command=self._reset_filters,
            bg=PANEL_ALT_BG,
            fg=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 9, "bold"),
            padx=10,
            pady=4,
        ).grid(row=0, column=4, sticky="e", padx=(10, 0))
        self.visible_count_label = tk.Label(filter_bar, text="", bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_MONO, 8))
        self.visible_count_label.grid(row=0, column=5, sticky="e", padx=(12, 0))

        main = tk.Frame(container, bg=APP_BG)
        main.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        main.columnconfigure(0, weight=4)
        main.columnconfigure(1, weight=1, minsize=360)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=APP_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        table_card = tk.Frame(left, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=10, pady=10)
        table_card.grid(row=0, column=0, sticky="nsew")
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(1, weight=1)
        table_header = tk.Frame(table_card, bg=PANEL_BG)
        table_header.grid(row=0, column=0, sticky="ew")
        table_header.columnconfigure(0, weight=1)
        tk.Label(table_header, text="Пиры", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.table_status_label = tk.Label(table_header, text="", bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_MONO, 8))
        self.table_status_label.grid(row=0, column=1, sticky="e")

        columns = (
            "status",
            "name",
            "vpn_ip",
            "diagnosis",
            "location",
            "channel",
            "handshake",
            "rx",
            "tx",
            "current",
            "share",
            "today",
            "total",
        )
        self.peer_tree = ttk.Treeview(table_card, columns=columns, show="headings", style="Shell.Treeview", selectmode="browse")
        headings = {
            "status": "Статус",
            "name": "Пир",
            "vpn_ip": "Виртуальный IP",
            "diagnosis": "Диагноз",
            "location": "Площадка",
            "channel": "Канал",
            "handshake": "Время отклика",
            "rx": "Входящий",
            "tx": "Исходящий",
            "current": "Всего",
            "share": "Доля",
            "today": "Сегодня",
            "total": "Всего",
        }
        widths = {
            "status": 72,
            "name": 128,
            "vpn_ip": 116,
            "diagnosis": 116,
            "location": 190,
            "channel": 82,
            "handshake": 112,
            "rx": 110,
            "tx": 110,
            "current": 110,
            "share": 72,
            "today": 104,
            "total": 104,
        }
        for column in columns:
            self.peer_tree.heading(column, text=headings[column])
            self.peer_tree.column(
                column,
                width=widths[column],
                anchor="w",
                stretch=column in {"name", "vpn_ip", "current", "today", "total", "location"},
            )
        self.peer_tree.tag_configure("active", background="#0d2029", foreground=TEXT_PRIMARY)
        self.peer_tree.tag_configure("inactive", background=ROW_INACTIVE_BG, foreground=TEXT_MUTED)
        self.peer_tree.tag_configure("issue_top", background="#103225", foreground=TEXT_PRIMARY)
        self.peer_tree.tag_configure("issue_offline", background="#2f2516", foreground=TEXT_PRIMARY)
        self.peer_tree.tag_configure("issue_stale", background="#2f2516", foreground=TEXT_PRIMARY)
        self.peer_tree.tag_configure("issue_mtu", background="#37181c", foreground=TEXT_PRIMARY)
        self.peer_tree.tag_configure("issue_ok", background="#0d2029", foreground=TEXT_PRIMARY)
        self.peer_tree.bind("<<TreeviewSelect>>", self._on_peer_tree_select)
        peer_scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.peer_tree.yview, style="Shell.Vertical.TScrollbar")
        self.peer_tree.configure(yscrollcommand=peer_scroll.set)
        self.peer_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        peer_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))

        right = tk.Frame(main, bg=APP_BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        detail_card = tk.Frame(right, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=9, pady=9)
        detail_card.grid(row=0, column=0, sticky="nsew")
        detail_card.columnconfigure(0, weight=1)
        tk.Label(detail_card, text="Выбранный пир", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._detail_header_label = tk.Label(
            detail_card,
            text="Выберите строку",
            bg=PANEL_BG,
            fg=TEXT_PRIMARY,
            font=(FONT_UI, 16, "bold"),
            wraplength=360,
            justify="left",
            anchor="w",
        )
        self._detail_header_label.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._detail_subtitle_label = tk.Label(
            detail_card,
            text="Локация, endpoint, трафик и MTU выбранного пира.",
            bg=PANEL_BG,
            fg=TEXT_MUTED,
            font=(FONT_UI, 8),
            wraplength=360,
            justify="left",
            anchor="w",
        )
        self._detail_subtitle_label.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._detail_note_label = tk.Label(
            detail_card,
            text="",
            bg=ACCENT_SOFT,
            fg=ACCENT,
            font=(FONT_UI, 9, "bold"),
            wraplength=360,
            justify="left",
            anchor="w",
            padx=8,
            pady=6,
        )
        self._detail_note_label.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        details_grid = tk.Frame(detail_card, bg=PANEL_BG)
        details_grid.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        details_grid.columnconfigure(1, weight=1)
        detail_rows = [
            ("endpoint", "ENDPOINT"),
            ("active", "Статус"),
            ("handshake", "HANDSHAKE"),
            ("server_ping", "Ping"),
            ("packet_loss", "Loss"),
            ("current", "Поток"),
            ("share", "Доля"),
            ("recommended_mtu", "MTU target"),
            ("today", "Сегодня"),
            ("total", "Накоплено"),
        ]
        for row_index, (key, title) in enumerate(detail_rows):
            label = tk.Label(details_grid, text=title, bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 8, "bold"), anchor="w")
            label.grid(row=row_index, column=0, sticky="w", pady=2)
            value = tk.Label(
                details_grid,
                text="—",
                bg=PANEL_BG,
                fg=TEXT_PRIMARY,
                font=(FONT_MONO, 8),
                wraplength=340,
                justify="left",
                anchor="w",
            )
            value.grid(row=row_index, column=1, sticky="ew", pady=2, padx=(8, 0))
            self._detail_value_labels[key] = value

        detail_actions = tk.Frame(detail_card, bg=PANEL_BG)
        detail_actions.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        detail_actions.columnconfigure(0, weight=1)
        detail_actions.columnconfigure(1, weight=1)
        tk.Button(
            detail_actions,
            text="Копировать endpoint",
            command=self._copy_selected_endpoint,
            bg=PANEL_ALT_BG,
            fg=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 8, "bold"),
            padx=6,
            pady=4,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        tk.Button(
            detail_actions,
            text="Профиль телефона",
            command=self._copy_selected_phone_profile,
            bg=PANEL_ALT_BG,
            fg=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT,
            highlightthickness=1,
            highlightbackground=BORDER_BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 8, "bold"),
            padx=6,
            pady=4,
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0))

        event_box = tk.Frame(detail_card, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1, padx=8, pady=8)
        event_box.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        tk.Label(event_box, text="Последние события", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_UI, 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self._detail_event_labels.clear()
        for row_index in range(4):
            event_label = tk.Label(
                event_box,
                text="—",
                bg=PANEL_BG,
                fg=TEXT_MUTED,
                font=(FONT_UI, 8),
                anchor="w",
                justify="left",
                wraplength=340,
            )
            event_label.grid(row=row_index + 1, column=0, sticky="ew", pady=2)
            self._detail_event_labels.append(event_label)

        footer_row = tk.Frame(container, bg=APP_BG)
        footer_row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        footer_row.columnconfigure(0, weight=1)
        self.footer_label = tk.Label(footer_row, text="", bg=APP_BG, fg=TEXT_MUTED, font=(FONT_MONO, 8))
        self.footer_label.grid(row=0, column=0, sticky="w")
        self.connection_label = tk.Label(footer_row, text="", bg=APP_BG, fg=TEXT_MUTED, font=(FONT_MONO, 8))
        self.connection_label.grid(row=0, column=1, sticky="e")

    def _create_metric_card(
        self,
        parent: tk.Frame,
        title: str,
        accent_color: str,
        show_secondary_value: bool = False,
    ) -> tuple[tk.Frame, tk.Label, Optional[tk.Label], tk.Label]:
        card = tk.Frame(parent, bg=PANEL_BG, highlightbackground=BORDER_BG, highlightthickness=1)
        accent = tk.Frame(card, bg=accent_color, height=3)
        accent.pack(side="top", fill="x")
        body = tk.Frame(card, bg=PANEL_BG, padx=12, pady=10)
        body.pack(side="top", fill="both", expand=True)
        tk.Label(body, text=title, bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 8, "bold")).pack(anchor="w")
        value_row = tk.Frame(body, bg=PANEL_BG)
        value_row.pack(fill="x", pady=(4, 0))
        value_label = tk.Label(value_row, text="—", bg=PANEL_BG, fg=TEXT_PRIMARY, font=(FONT_MONO, 14, "bold"))
        value_label.pack(side="left", anchor="w")
        secondary_value_label: Optional[tk.Label] = None
        if show_secondary_value:
            secondary_value_label = tk.Label(
                value_row,
                text="—",
                bg=PANEL_BG,
                fg=ACCENT_2,
                font=(FONT_MONO, 14, "bold"),
                anchor="e",
            )
            secondary_value_label.pack(side="right", anchor="e")
        note_label = tk.Label(body, text="", bg=PANEL_BG, fg=TEXT_MUTED, font=(FONT_UI, 8), wraplength=260, justify="left")
        note_label.pack(anchor="w", pady=(4, 0))
        return card, value_label, secondary_value_label, note_label

    def _set_status_mode(self, mode: str) -> None:
        self._status_mode = mode
        if mode == "online":
            self.state_label.configure(text="Система: Норма", bg=APP_BG, fg=ACCENT)
            top_status_labels = getattr(self, "_top_status_labels", {})
            if "vpn" in top_status_labels:
                top_status_labels["vpn"].configure(text="Сервер VPN: Онлайн", fg=ACCENT)
        elif mode == "connecting":
            self.state_label.configure(text="Система: Синхронизация", bg=APP_BG, fg=WARN_TEXT)
            top_status_labels = getattr(self, "_top_status_labels", {})
            if "vpn" in top_status_labels:
                top_status_labels["vpn"].configure(text="Сервер VPN: проверка", fg=WARN_TEXT)
        else:
            self.state_label.configure(text="Система: Ошибка", bg=APP_BG, fg=ERROR_TEXT)
            top_status_labels = getattr(self, "_top_status_labels", {})
            if "vpn" in top_status_labels:
                top_status_labels["vpn"].configure(text="Сервер VPN: нет связи", fg=ERROR_TEXT)

    def _refresh_status_indicator(self) -> None:
        canvas = getattr(self, "_status_indicator_canvas", None)
        dot = getattr(self, "_status_indicator_dot", None)
        if canvas is None or dot is None:
            return
        mode = getattr(self, "_status_mode", "offline")
        if mode == "online":
            fill = ACCENT if self._status_blink_on else "#163529"
            outline = ACCENT
        elif mode == "connecting":
            fill = WARN_TEXT
            outline = WARN_TEXT
        else:
            fill = ERROR_TEXT
            outline = ERROR_TEXT
        canvas.itemconfigure(dot, fill=fill, outline=outline)

    def _schedule_status_indicator_tick(self) -> None:
        if self._closed:
            return
        if getattr(self, "_status_mode", "offline") == "online":
            self._status_blink_on = not self._status_blink_on
        else:
            self._status_blink_on = False
        self._refresh_status_indicator()
        if self._status_blink_job is not None:
            try:
                self.root.after_cancel(self._status_blink_job)
            except Exception:
                pass
        self._status_blink_job = self.root.after(350, self._schedule_status_indicator_tick)

    def _render_trend_graph(self, model: Dict[str, object]) -> None:
        canvas = getattr(self, "_trend_canvas", None)
        if canvas is None:
            return

        current_bps = _coerce_float(model.get("current_total_bps"))
        rx_bps = _coerce_float(model.get("current_rx_bps"))
        tx_bps = _coerce_float(model.get("current_tx_bps"))
        capacity_bps = _coerce_float(model.get("bandwidth_limit_bps"))
        utilization_value = model.get("bandwidth_utilization_value")
        utilization = _coerce_float(utilization_value) if utilization_value is not None else (
            (current_bps / capacity_bps * 100.0) if capacity_bps > 0 else 0.0
        )
        signature = (
            current_bps,
            rx_bps,
            tx_bps,
            capacity_bps,
            round(utilization, 2),
            str(model.get("updated_label", "")),
        )
        if signature == self._last_trend_signature:
            trend_value_label = getattr(self, "_trend_value_label", None)
            if trend_value_label is not None:
                trend_value_label.configure(
                    text=(
                        f"сейчас {collector.format_rate(current_bps)} | "
                        f"пик {model.get('daily_peak', 'н/д')} | загрузка {utilization:.1f}%"
                    )
                )
            return

        if current_bps > 0:
            history = list(getattr(self, "_traffic_history", []))
            history.append(current_bps)
            history_limit = max(int(getattr(self, "_traffic_history_limit", 36)), 1)
            history = history[-history_limit:]
            self._traffic_history = history
        else:
            history = list(getattr(self, "_traffic_history", []))
            if not history:
                history = [0.0]
            self._traffic_history = history

        history_max = max(self._traffic_history) if self._traffic_history else 1.0
        history_max = max(history_max, current_bps, rx_bps, tx_bps)
        history_max = max(history_max, 1.0)

        canvas.delete("all")
        width = max(int(canvas.winfo_width() or 0), 1)
        height = max(int(canvas.winfo_height() or 0), 1)
        inner_pad = 8
        plot_width = max(width - inner_pad * 2, 1)
        self._trend_phase = (self._trend_phase + 6) % max(plot_width, 1)

        grid_color = "#1a2a34"
        soft_grid = "#132820"
        for x in range(inner_pad, width - inner_pad + 1, 56):
            canvas.create_line(x, inner_pad + 14, x, height - inner_pad, fill=grid_color, width=1)
        for y in range(inner_pad + 18, height - inner_pad + 1, 34):
            canvas.create_line(inner_pad, y, width - inner_pad, y, fill=grid_color, width=1)
        for x in range(inner_pad + 28, width - inner_pad + 1, 168):
            canvas.create_line(x, inner_pad + 14, x, height - inner_pad, fill=soft_grid, width=1, dash=(2, 8))
        canvas.create_text(
            inner_pad,
            inner_pad + 4,
            anchor="w",
            fill=TEXT_MUTED,
            font=("Consolas", 7),
            text="detail scale: live RX/TX bars + 90 snapshot trend",
        )
        canvas.create_text(
            width - inner_pad,
            inner_pad + 4,
            anchor="e",
            fill=TEXT_MUTED,
            font=("Consolas", 7),
            text="RX blue / TX green",
        )

        def _draw_metric_bar(y_top: int, label: str, value_bps: float, limit_bps: float, fill_color: str, outline: str) -> None:
            label_width = 162
            value_width = 128
            bar_left = inner_pad + label_width
            bar_right = width - inner_pad - value_width
            bar_width = max(bar_right - bar_left, 1)
            ratio = value_bps / limit_bps if limit_bps > 0 else 0.0
            fill = max(0.0, min(ratio, 1.0))
            label_y = y_top
            bar_top = y_top + 9
            bar_bottom = bar_top + 10
            canvas.create_text(inner_pad, label_y, anchor="w", fill=TEXT_MUTED, font=("Consolas", 8, "bold"), text=label)
            canvas.create_rectangle(bar_left, bar_top, bar_right, bar_bottom, outline=outline, fill="#0c151d")
            canvas.create_rectangle(bar_left, bar_top, bar_left + int(bar_width * fill), bar_bottom, outline="", fill=fill_color)
            canvas.create_text(
                width - inner_pad,
                bar_top + 5,
                anchor="e",
                fill=TEXT_PRIMARY,
                font=("Consolas", 8),
                text=collector.format_rate(value_bps),
            )

        rx_color = ACCENT_2 if rx_bps < capacity_bps * 0.6 else "#8dd8ff"
        tx_color = ACCENT if tx_bps < capacity_bps * 0.6 else "#8ff0be"
        total_color = ACCENT if utilization < 80 else (WARN_TEXT if utilization < 95 else ERROR_TEXT)

        live_flow_limit = max(current_bps, rx_bps, tx_bps, 1.0)
        _draw_metric_bar(
            inner_pad + 12,
            "RX / входящий",
            rx_bps,
            live_flow_limit,
            rx_color,
            BORDER_BG,
        )
        _draw_metric_bar(
            inner_pad + 36,
            "TX / исходящий",
            tx_bps,
            live_flow_limit,
            tx_color,
            BORDER_BG,
        )

        # capacity bar
        bar_fill = max(0.0, min(utilization / 100.0, 1.0))
        bar_top = inner_pad + 60
        bar_bottom = inner_pad + 72
        canvas.create_rectangle(inner_pad, bar_top, width - inner_pad, bar_bottom, outline=BORDER_BG, fill="#0b141b")
        canvas.create_rectangle(
            inner_pad,
            bar_top,
            inner_pad + int(plot_width * bar_fill),
            bar_bottom,
            outline="",
            fill=total_color,
        )
        canvas.create_text(
            width - inner_pad,
            bar_top + 7,
            anchor="e",
            fill=TEXT_MUTED,
            font=("Consolas", 8, "bold"),
            text=f"Канал {collector.format_rate(current_bps)} / лимит {collector.format_rate(capacity_bps)}",
        )

        # sparkline
        spark_top = inner_pad + 82
        spark_bottom = height - inner_pad
        spark_height = max(spark_bottom - spark_top, 1)
        spark_width = max(plot_width, 1)
        baseline_y = spark_bottom - 2
        canvas.create_line(inner_pad, baseline_y, width - inner_pad, baseline_y, fill="#172b35", width=1)
        canvas.create_text(inner_pad, spark_bottom - 4, anchor="sw", fill=TEXT_MUTED, font=("Consolas", 7), text="-90s")
        canvas.create_text(width - inner_pad, spark_bottom - 4, anchor="se", fill=TEXT_MUTED, font=("Consolas", 7), text="now")
        if len(self._traffic_history) == 1:
            value = self._traffic_history[0]
            fill_height = int(spark_height * (value / history_max))
            canvas.create_rectangle(
                inner_pad,
                spark_bottom - fill_height,
                width - inner_pad,
                spark_bottom,
                outline="",
                fill="#123429",
            )
        else:
            points = []
            fill_points = [inner_pad, spark_bottom]
            count = len(self._traffic_history)
            for idx, value in enumerate(self._traffic_history):
                x = inner_pad + int((spark_width * idx) / max(count - 1, 1))
                y = spark_bottom - int((value / history_max) * spark_height)
                points.extend([x, y])
                fill_points.extend([x, y])
                stem_color = ACCENT if idx == count - 1 else "#1b4d3a"
                canvas.create_line(x, baseline_y, x, y, fill=stem_color, width=2 if idx == count - 1 else 1)
            if len(points) >= 4:
                canvas.create_polygon(*fill_points, width=0, smooth=True, fill="#10231d")
                canvas.create_line(*points, fill="#1c4a39", width=6, smooth=True)
                canvas.create_line(*points, fill=ACCENT, width=3, smooth=True)

        if self._traffic_history:
            cursor_x = inner_pad + self._trend_phase
            cursor_x = min(max(cursor_x, inner_pad), width - inner_pad)
            canvas.create_line(cursor_x, spark_top, cursor_x, spark_bottom, fill="#2f5e49", width=1, dash=(3, 4))
            latest = self._traffic_history[-1]
            latest_y = spark_bottom - int((latest / history_max) * spark_height)
            canvas.create_oval(cursor_x - 4, latest_y - 4, cursor_x + 4, latest_y + 4, outline="", fill=ACCENT)
            canvas.create_oval(cursor_x - 7, latest_y - 7, cursor_x + 7, latest_y + 7, outline=ACCENT, width=1)

        trend_value_label = getattr(self, "_trend_value_label", None)
        if trend_value_label is not None:
            trend_value_label.configure(
                text=(
                    f"сейчас {collector.format_rate(current_bps)} | "
                    f"пик {model.get('daily_peak', 'н/д')} | загрузка {utilization:.1f}%"
                )
            )
        self._last_trend_signature = signature

    def _reset_filters(self) -> None:
        self._search_query.set("")
        self._active_only.set(False)
        self._status_filter.set("Все")
        self._location_filter.set("Все")
        self._period_filter.set("Сегодня")
        self._set_quick_filter("all")

    def _set_quick_filter(self, value: str) -> None:
        self._quick_filter = value or "all"
        if self._quick_filter != "all" and self._status_filter.get() != "Все":
            self._status_filter.set("Все")
        self._refresh_quick_filter_buttons()
        self._apply_peer_filter()

    def _refresh_quick_filter_buttons(self) -> None:
        for key, button in self._quick_filter_buttons.items():
            active = key == self._quick_filter
            button.configure(
                bg=ACCENT_SOFT if active else PANEL_ALT_BG,
                fg=ACCENT if active else TEXT_MUTED,
                highlightbackground=ACCENT if active else BORDER_BG,
            )

    def _copy_to_clipboard(self, text: str, status_message: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._status_base_text = HEADER_BASE_TEXT
            self.updated_label.configure(text=f"{HEADER_BASE_TEXT} • {status_message}", bg=STATUS_BG, fg=STATUS_TEXT)
        except Exception as exc:
            self._append_runtime_error("clipboard", exc)

    def _copy_selected_endpoint(self) -> None:
        peer = self._peer_rows_by_key.get(self._selected_peer_key or "", {})
        endpoint = str(peer.get("endpoint", "") or "")
        if not endpoint:
            return
        self._copy_to_clipboard(endpoint, f"endpoint скопирован: {endpoint}")

    def _copy_selected_phone_profile(self) -> None:
        peer = self._peer_rows_by_key.get(self._selected_peer_key or "", {})
        endpoint = str(peer.get("endpoint", "") or "")
        mtu = str(peer.get("recommended_mtu", "1280 B")).replace(" B", "")
        if not endpoint:
            return
        profile = f"Endpoint = {endpoint}\nMTU = {mtu}\nPersistentKeepalive = 25"
        self._copy_to_clipboard(profile, "профиль телефона скопирован")

    def _refresh_location_filter_options(self) -> None:
        combo = getattr(self, "_location_filter_combo", None)
        if combo is None:
            return
        locations = sorted(
            {
                _location_bucket(peer.get("endpoint_location"))
                for peer in self._all_peer_rows
                if _location_bucket(peer.get("endpoint_location"))
            }
        )
        values = ["Все"] + locations[:24]
        combo.configure(values=values)
        if self._location_filter.get() not in values:
            self._location_filter.set("Все")

    def _update_metric_cards(self, model: Dict[str, object]) -> None:
        current_total = str(model.get("current_total", "—"))
        bandwidth_limit = str(model.get("bandwidth_limit", "—"))
        bandwidth_utilization = str(model.get("bandwidth_utilization", "н/д"))
        bandwidth_headroom = str(model.get("bandwidth_headroom", "н/д"))
        current_rx = str(model.get("current_rx", "—"))
        current_tx = str(model.get("current_tx", "—"))
        active_connections = _coerce_int(model.get("active_connections"))
        total_peers = _coerce_int(model.get("total_peers"))
        offline_connections = _coerce_int(model.get("offline_connections"))
        peer_rows = list(self._all_peer_rows)
        top_peer = peer_rows[0] if peer_rows else {}
        updated_clock = str(model.get("updated_clock", "—"))
        updated_day = str(model.get("updated_day", ""))
        age_label = str(model.get("age_label", "н/д"))
        refresh_seconds = _coerce_float(model.get("refresh_seconds"))
        server_ping = str(model.get("server_ping", "н/д"))
        server_packet_loss = str(model.get("server_packet_loss", "н/д"))
        server_packet_loss_value = _coerce_float(model.get("server_packet_loss_value"))
        server_load = str(model.get("server_load", "н/д"))
        server_uptime = str(model.get("server_uptime", "н/д"))
        daily_average = str(model.get("daily_average", "н/д"))
        daily_peak = str(model.get("daily_peak", "н/д"))
        utilization_value = max(0.0, min(_coerce_float(model.get("bandwidth_utilization_value")), 100.0))
        issue_count = sum(
            1 for peer in peer_rows if _peer_diagnostic_state(peer)["key"] in {"offline", "stale", "mtu"}
        )
        top_count = sum(1 for peer in peer_rows if _peer_diagnostic_state(peer)["key"] == "top")
        snapshot_age_seconds = model.get("snapshot_age_seconds")
        snapshot_is_fresh = snapshot_age_seconds is None or _coerce_int(snapshot_age_seconds) <= 10
        ops_summary_label = getattr(self, "_ops_summary_label", None)
        if ops_summary_label is not None:
            leader = str(top_peer.get("name") or top_peer.get("vpn_ip") or "—") if top_peer else "—"
            ops_summary_label.configure(
                text=(
                    f"{total_peers} пиров • {active_connections} активны • {offline_connections} без связи • "
                    f"{issue_count} требуют внимания • лидер {leader} • данные {age_label}"
                ),
                fg=TEXT_PRIMARY if snapshot_is_fresh else WARN_TEXT,
            )

        self._card_value_labels["channel"].configure(text=current_total)
        channel_secondary = self._card_secondary_value_labels.get("channel")
        if channel_secondary is not None:
            channel_secondary.configure(text=bandwidth_utilization)
        channel_row_labels = getattr(self, "_channel_row_value_labels", {})
        for key, value in {
            "rx": current_rx,
            "tx": current_tx,
            "peak": f"{daily_peak} / {model.get('daily_peak_utilization', 'н/д')}",
            "average": daily_average,
            "limit": bandwidth_limit,
            "headroom": bandwidth_headroom,
        }.items():
            label = channel_row_labels.get(key)
            if label is not None:
                label.configure(text=str(value))
        meter = getattr(self, "_channel_meter_canvas", None)
        if meter is not None:
            meter.delete("all")
            width = max(int(meter.winfo_width() or 360), 1)
            height = max(int(meter.winfo_height() or 13), 1)
            fill_width = max(int(width * utilization_value / 100.0), 2 if utilization_value > 0 else 0)
            fill_color = ACCENT if utilization_value < 80 else (WARN_TEXT if utilization_value < 95 else ERROR_TEXT)
            meter.create_rectangle(0, 2, width, height - 2, outline=BORDER_BG, fill="#1d2b38")
            meter.create_rectangle(0, 2, fill_width, height - 2, outline="", fill=fill_color)

        active_label = _format_percent_pair(active_connections, total_peers)
        offline_label = _format_percent_pair(offline_connections, total_peers)
        self._card_value_labels["peers"].configure(text=f"{total_peers}")
        self._card_note_labels["peers"].configure(
            text=f"Онлайн {active_label} | Оффлайн {offline_label} | проблем {issue_count} | топ {top_count}"
        )

        server_online = 1 if str(model.get("container_status", "")).lower() in {"работает", "running"} else 0
        self._card_value_labels["server"].configure(text="1")
        self._card_note_labels["server"].configure(
            text=f"Онлайн {server_online} (100%) | ping {server_ping} | loss {server_packet_loss} | load {server_load}"
        )

        self._card_value_labels["snapshot"].configure(text="1")
        self._card_note_labels["snapshot"].configure(text=f"{updated_day} {updated_clock} | age {age_label} | шаг {refresh_seconds:g}s")

        leader_name = str(top_peer.get("name", "—")) if top_peer else "—"
        leader_rate = str(top_peer.get("current", "—")) if top_peer else "—"
        leader_share = str(top_peer.get("share", "—")) if top_peer else "—"
        leader_location = str(top_peer.get("endpoint_location", ""))
        self._card_value_labels["leader"].configure(text=leader_name if leader_name else "—")
        leader_note_parts = [part for part in (leader_location, leader_rate, leader_share) if part]
        self._card_note_labels["leader"].configure(text=" | ".join(leader_note_parts))

        for key, value in {
            "time": f"Время: {updated_clock}",
            "loss": f"Потери: {server_packet_loss}",
            "uptime": f"Аптайм: {server_uptime}",
            "refresh": f"Обновление: {refresh_seconds:g} сек",
        }.items():
            label = getattr(self, "_top_status_labels", {}).get(key)
            if label is not None:
                fg = WARN_TEXT if key == "loss" and server_packet_loss_value > 2 else TEXT_MUTED
                label.configure(text=str(value), fg=fg)
        for key, value in {
            "rx": current_rx,
            "tx": current_tx,
            "total": current_total,
            "peak": daily_peak,
            "average": daily_average,
            "utilization": bandwidth_utilization,
        }.items():
            label = getattr(self, "_trend_stat_labels", {}).get(key)
            if label is not None:
                label.configure(text=str(value))
        self._render_trend_graph(model)

    def _apply_peer_filter(self) -> None:
        query = _normalize_query(self._search_query.get())
        active_only = bool(self._active_only.get())
        status_filter = str(self._status_filter.get() or "Все")
        location_filter = str(self._location_filter.get() or "Все")
        quick_filter = str(getattr(self, "_quick_filter", "all") or "all")
        self._filtered_peer_rows = [
            peer
            for peer in self._all_peer_rows
            if _peer_matches_filter(peer, query, active_only, status_filter, location_filter, quick_filter)
        ]
        self._populate_peers(self._filtered_peer_rows)
        self._update_peer_counts()

    def _update_peer_counts(self) -> None:
        total = len(self._all_peer_rows)
        visible = len(self._filtered_peer_rows)
        active = sum(1 for peer in self._all_peer_rows if bool(peer.get("active_value")))
        query = _normalize_query(self._search_query.get())
        mode_parts = []
        if query:
            mode_parts.append(f"поиск: {query}")
        if bool(self._active_only.get()):
            mode_parts.append("только активные")
        if self._status_filter.get() != "Все":
            mode_parts.append(f"статус: {self._status_filter.get()}")
        if self._location_filter.get() != "Все":
            mode_parts.append(f"площадка: {self._location_filter.get()}")
        if self._period_filter.get():
            mode_parts.append(f"период: {self._period_filter.get()}")
        if getattr(self, "_quick_filter", "all") != "all":
            quick_titles = {
                "top": "топ трафика",
                "offline": "без связи",
                "stale": "тихие >10м",
                "mtu": "MTU issue",
                "active": "активные сейчас",
                "issues": "требуют внимания",
            }
            mode_parts.append(f"сценарий: {quick_titles.get(self._quick_filter, self._quick_filter)}")
        mode_text = f" | {'; '.join(mode_parts)}" if mode_parts else ""
        self.visible_count_label.configure(text=f"Показано {visible}/{total} | активных {active}{mode_text}")
        self.table_status_label.configure(text=f"{visible} peers")
        if not self._selected_peer_key and self._filtered_peer_rows:
            self._select_peer(self._filtered_peer_rows[0].get("public_key", ""))
        elif self._selected_peer_key and self._selected_peer_key not in {str(peer.get("public_key", "")) for peer in self._filtered_peer_rows}:
            if self._filtered_peer_rows:
                self._select_peer(self._filtered_peer_rows[0].get("public_key", ""))
            else:
                self._select_peer(None)
        else:
            self._render_peer_details(self._peer_rows_by_key.get(self._selected_peer_key or "", {}))

    def _select_peer(self, public_key: Optional[str]) -> None:
        next_key = str(public_key) if public_key else None
        current_selection = self.peer_tree.selection()
        if next_key == self._selected_peer_key and (
            not next_key or (len(current_selection) == 1 and current_selection[0] == next_key)
        ):
            self._render_peer_details(self._peer_rows_by_key.get(self._selected_peer_key or "", {}))
            return

        self._selected_peer_key = next_key
        if not self._selected_peer_key:
            self._suppress_tree_select_event = True
            try:
                self.peer_tree.selection_remove(self.peer_tree.selection())
            finally:
                self._suppress_tree_select_event = False
            self._render_peer_details({})
            return
        if self.peer_tree.exists(self._selected_peer_key):
            self._suppress_tree_select_event = True
            try:
                self.peer_tree.selection_set(self._selected_peer_key)
                self.peer_tree.see(self._selected_peer_key)
            finally:
                self._suppress_tree_select_event = False
        self._render_peer_details(self._peer_rows_by_key.get(self._selected_peer_key, {}))

    def _on_peer_tree_select(self, _event: object = None) -> None:
        if self._suppress_tree_select_event:
            return
        selection = self.peer_tree.selection()
        if not selection or selection[0] == self._selected_peer_key:
            return
        self._select_peer(selection[0])

    def _render_peer_details(self, peer: Dict[str, object]) -> None:
        if not peer:
            self._detail_header_label.configure(text="Выберите строку")
            self._detail_subtitle_label.configure(text="Локация, endpoint, трафик и MTU выбранного пира.")
            self._detail_note_label.configure(text="", bg=ACCENT_SOFT, fg=ACCENT)
            for value_label in self._detail_value_labels.values():
                value_label.configure(text="—")
            for event_label in self._detail_event_labels:
                event_label.configure(text="—", fg=TEXT_MUTED)
            return

        name = str(peer.get("name", ""))
        location = str(peer.get("endpoint_location", "")) or "нет данных"
        endpoint = str(peer.get("endpoint", "")) or "нет endpoint"
        path_mtu = str(peer.get("path_mtu", "н/д"))
        path_mtu_note = str(peer.get("path_mtu_note", ""))
        if path_mtu_note:
            path_mtu = f"{path_mtu} ({path_mtu_note})"
        interface_mtu = str(peer.get("interface_mtu", "н/д"))
        interface_mtu_note = str(peer.get("interface_mtu_note", ""))
        if interface_mtu_note:
            interface_mtu = f"{interface_mtu} ({interface_mtu_note})"
        recommended_mtu = str(peer.get("recommended_mtu", "н/д"))
        vpn_ip = str(peer.get("vpn_ip", "")) or "нет VPN IP"
        active = "активен" if bool(peer.get("active_value")) else "нет связи"
        handshake = str(peer.get("handshake", "н/д"))
        rx = str(peer.get("rx", "—"))
        tx = str(peer.get("tx", "—"))
        current = str(peer.get("current", "—"))
        share = str(peer.get("share", "—"))
        today = str(peer.get("today", "—"))
        total = str(peer.get("total", "—"))
        public_key_short = str(peer.get("public_key_short", "")) or "—"
        server_ping = str(peer.get("server_ping", "н/д"))
        packet_loss = str(peer.get("packet_loss", "н/д"))
        diagnostic = _peer_diagnostic_state(peer)
        if diagnostic["key"] in {"ok", "top"}:
            note = _peer_detail_note(peer)
        elif diagnostic["key"] == "mtu":
            note = "Пир виден, но профиль MTU требует сверки с target 1280."
        elif diagnostic["key"] == "stale":
            note = "Пир давно не давал свежий handshake."
        else:
            note = _peer_detail_note(peer)
        if diagnostic["level"] == "danger":
            note_bg, note_fg = ERROR_BG, ERROR_TEXT
        elif diagnostic["level"] == "warn":
            note_bg, note_fg = WARN_BG, WARN_TEXT
        else:
            note_bg, note_fg = ACCENT_SOFT, ACCENT

        def set_detail_value(key: str, text: object) -> None:
            label = self._detail_value_labels.get(key)
            if label is not None:
                label.configure(text=str(text))

        self._detail_header_label.configure(text=name or "Без имени")
        self._detail_subtitle_label.configure(text=f"{location} | {endpoint}")
        self._detail_note_label.configure(text=note, bg=note_bg, fg=note_fg)
        set_detail_value("location", location)
        set_detail_value("endpoint", endpoint)
        set_detail_value("path_mtu", path_mtu)
        set_detail_value("interface_mtu", interface_mtu)
        set_detail_value("recommended_mtu", recommended_mtu)
        set_detail_value("vpn_ip", vpn_ip)
        set_detail_value("active", active)
        set_detail_value("handshake", handshake)
        set_detail_value("server_ping", server_ping)
        set_detail_value("packet_loss", packet_loss)
        set_detail_value("rx", rx)
        set_detail_value("tx", tx)
        set_detail_value("current", current)
        set_detail_value("share", share)
        set_detail_value("today", today)
        set_detail_value("total", total)
        set_detail_value("public_key_short", public_key_short)
        event_items = [
            ("●", f"{handshake}: {'соединение активно' if bool(peer.get('active_value')) else 'нет свежего handshake'}", ACCENT if bool(peer.get("active_value")) else TEXT_MUTED),
            ("●", f"Трафик: {current} / доля {share}", CYAN),
            ("●", f"Ping {server_ping} / packet loss {packet_loss}", WARN_TEXT if packet_loss not in {"0.00%", "0%", "н/д"} else TEXT_MUTED),
            ("●", f"MTU target: {recommended_mtu}", WARN_TEXT if diagnostic["key"] == "mtu" else TEXT_MUTED),
        ]
        for label, event in zip(self._detail_event_labels, event_items):
            marker, text, color = event
            label.configure(text=f"{marker} {text}", fg=color)

    def _on_refresh_key(self, _event: object) -> str:
        self.request_refresh()
        return "break"

    def _cancel_refresh_timer(self) -> None:
        if self._refresh_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_after_id)
            except tk.TclError:  # pragma: no cover - shutdown edge case
                pass
            self._refresh_after_id = None

    def _schedule_refresh(self) -> None:
        self._cancel_refresh_timer()
        if self._closed:
            return
        self._refresh_after_id = self.root.after(self.refresh_ms, self._fire_refresh)

    def _fire_refresh(self) -> None:
        self._refresh_after_id = None
        self.request_refresh()

    def _cancel_refresh_result_poll(self) -> None:
        if self._refresh_result_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_result_after_id)
            except tk.TclError:  # pragma: no cover - shutdown edge case
                pass
            self._refresh_result_after_id = None

    def _schedule_refresh_result_poll(self) -> None:
        if self._closed or self._refresh_result_after_id is not None:
            return
        self._refresh_result_after_id = self.root.after(100, self._poll_refresh_results)

    def _poll_refresh_results(self) -> None:
        self._refresh_result_after_id = None
        if self._closed:
            return
        while True:
            try:
                kind, payload = self._refresh_results.get_nowait()
            except queue.Empty:
                break
            if kind == "success":
                self._handle_refresh_success(payload)  # type: ignore[arg-type]
            else:
                self._handle_refresh_error(payload)  # type: ignore[arg-type]
        self._schedule_refresh_result_poll()

    def _port_in_use(self) -> bool:
        return _test_local_port(self.local_port)

    def _ensure_remote_monitoring_started(self) -> None:
        if not self.manage_remote_monitoring or self._remote_monitoring_started:
            return
        _run_remote_monitoring_control(self.host, self.ssh_user, self.key_path, "start")
        self._remote_monitoring_started = True

    def _stop_remote_monitoring(self) -> None:
        if not self.manage_remote_monitoring:
            return
        try:
            _run_remote_monitoring_control(self.host, self.ssh_user, self.key_path, "stop")
        except Exception as exc:  # pragma: no cover - network dependent cleanup
            _append_ssh_log(f"remote monitoring stop failed: {exc!r}")
            _append_debug_log(f"remote monitoring stop failed: {exc!r}")
        finally:
            self._remote_monitoring_started = False

    def _ensure_tunnel(self) -> None:
        if self._closed:
            return
        self._ensure_remote_monitoring_started()
        if self._port_in_use():
            return
        if self._ssh_process is not None and self._ssh_process.poll() is not None:
            self._ssh_process = None
        if self._ssh_process is None:
            self._ssh_process = _start_ssh_tunnel(self.host, self.ssh_user, self.key_path, self.local_port, self.remote_port)
        deadline = time.monotonic() + _SSH_TUNNEL_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self._closed:
                return
            if self._port_in_use():
                return
            if self._ssh_process is not None and self._ssh_process.poll() is not None:
                return_code = self._ssh_process.returncode
                _append_ssh_log(f"tunnel exited with code {return_code}")
                self._ssh_process = None
                raise RuntimeError(f"SSH tunnel exited with code {return_code}")
            time.sleep(0.25)
        if self._ssh_process is not None:
            try:
                self._ssh_process.terminate()
                self._ssh_process.wait(timeout=1)
            except Exception:  # pragma: no cover - best effort cleanup
                pass
            _append_ssh_log("tunnel timed out and was terminated")
            self._ssh_process = None
        raise TimeoutError(f"Tunnel did not open on 127.0.0.1:{self.local_port}")

    def _invalidate_tunnel(self) -> None:
        if self._ssh_process is None:
            return
        try:
            if self._ssh_process.poll() is None:
                self._ssh_process.terminate()
                try:
                    self._ssh_process.wait(timeout=1)
                except Exception:
                    self._ssh_process.kill()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        finally:
            self._ssh_process = None

    def request_refresh(self) -> None:
        if self._closed or self._refresh_in_flight:
            return
        self._cancel_refresh_timer()
        self._refresh_in_flight = True
        if self._last_model is None:
            self._set_status_mode("connecting")
            self.updated_label.configure(text=f"{HEADER_BASE_TEXT} • проверка ssh-туннеля и загрузка snapshot...")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            self._ensure_tunnel()
            summary = fetch_summary(self.dashboard_url)
            model = build_view_model(summary, self.dashboard_url, self.refresh_seconds)
        except Exception as exc:  # pragma: no cover - network and runtime dependent
            _append_debug_log(f"refresh_worker error: {exc!r}")
            _append_debug_log(traceback.format_exc())
            self._invalidate_tunnel()
            self._refresh_results.put(("error", exc))
            return
        self._refresh_results.put(("success", model))

    def _handle_refresh_success(self, model: Dict[str, object]) -> None:
        if self._closed:
            return
        try:
            snapshot_label = str(model.get("updated_label", ""))
            age_label = str(model.get("age_label", "н/д"))
            refresh_seconds = _coerce_float(model.get("refresh_seconds"))
            repeated_snapshot = self._last_model is not None and self._last_snapshot_label == snapshot_label

            self._refresh_in_flight = False
            self._last_model = model
            self._last_snapshot_label = snapshot_label

            if repeated_snapshot:
                status_text = _format_sync_status_text(snapshot_label, age_label, refresh_seconds)
                self._status_base_text = status_text
                self.updated_label.configure(text=status_text)
                self._card_value_labels["snapshot"].configure(text=str(model.get("updated_clock", snapshot_label)))
                self._card_note_labels["snapshot"].configure(
                    text=f"{model.get('updated_day', '')} | age {age_label} | шаг {refresh_seconds:g}s"
                )
                self._render_trend_graph(model)
                self._set_warning_text(model.get("warnings", []))
                source_url = str(model.get("source_url") or getattr(self, "dashboard_url", ""))
                age_seconds = model.get("snapshot_age_seconds")
                fresh_label = "данные свежие" if age_seconds is None or _coerce_int(age_seconds) <= 10 else "данные устарели"
                self.footer_label.configure(text=f"Источник: {source_url} | age {age_label} | {fresh_label} | сервер доступен")
                self.connection_label.configure(text="LINK UP")
                self._set_status_mode("online")
                self._schedule_refresh()
                return

            previous_selection = self._selected_peer_key
            self._all_peer_rows = list(model.get("peer_rows", []))
            self._peer_rows_by_key = {
                str(peer.get("public_key", "")): peer for peer in self._all_peer_rows if str(peer.get("public_key", ""))
            }
            self._refresh_location_filter_options()

            self.root.title(f"Autostop VPN Monitor :: {str(model['bandwidth_state_label']).upper()}")
            self._set_status_mode("online")
            status_text = _format_sync_status_text(snapshot_label, age_label, refresh_seconds)
            self._status_base_text = status_text
            self.updated_label.configure(text=status_text)
            self._apply_peer_filter()
            self._update_metric_cards(model)
            self._set_warning_text(model["warnings"])
            if previous_selection and previous_selection in self._peer_rows_by_key:
                self._select_peer(previous_selection)
            elif not self._filtered_peer_rows:
                self._select_peer(None)
            age_seconds = model.get("snapshot_age_seconds")
            fresh_label = "данные свежие" if age_seconds is None or _coerce_int(age_seconds) <= 10 else "данные устарели"
            self.footer_label.configure(text=f"Источник: {model['source_url']} | age {age_label} | {fresh_label} | сервер доступен")
            self.connection_label.configure(text="LINK UP")
            self._schedule_refresh()
        except Exception as exc:
            self._append_runtime_error("refresh_success", exc)
            self._handle_refresh_error(exc)

    def _handle_refresh_error(self, exc: Exception) -> None:
        if self._closed:
            return
        self._refresh_in_flight = False
        self._set_status_mode("offline")
        self._status_base_text = HEADER_BASE_TEXT
        self.updated_label.configure(text=f"{HEADER_BASE_TEXT} • Не удалось обновить данные: {exc}")
        self.connection_label.configure(text="LINK DOWN")
        if self._last_model is None:
            self.updated_label.configure(text=f"{HEADER_BASE_TEXT} • SYNC нет данных")
            self.footer_label.configure(text=f"Источник: {self.dashboard_url} | данных нет | сервер недоступен")
            self._set_warning_text([f"Ошибка загрузки: {exc}"])
            self._all_peer_rows = []
            self._filtered_peer_rows = []
            self._peer_rows_by_key = {}
            self._populate_peers([])
            self._update_metric_cards(
                {
                    "current_total": "н/д",
                    "bandwidth_limit": "н/д",
                    "bandwidth_utilization": "н/д",
                    "bandwidth_headroom": "н/д",
                    "active_connections": 0,
                    "total_peers": 0,
                    "updated_label": "—",
                    "age_label": "н/д",
                    "refresh_seconds": self.refresh_seconds,
                }
            )
            self._render_peer_details({})
        self._schedule_refresh()

    def _append_runtime_error(self, where: str, exc: Exception) -> None:
        _append_debug_log(f"{where}: {exc!r}")
        _append_debug_log(traceback.format_exc())

    def _report_callback_exception(self, exc: type, value: BaseException, tb: object) -> None:
        _append_debug_log(f"tk_callback: {exc.__name__}: {value!r}")
        _append_debug_log("".join(traceback.format_exception(exc, value, tb)))
        self._refresh_in_flight = False
        try:
            self._set_status_mode("offline")
            self._status_base_text = HEADER_BASE_TEXT
            self.updated_label.configure(text=f"{HEADER_BASE_TEXT} • tk callback error: {value}")
            self.connection_label.configure(text="LINK DOWN")
        except Exception:
            pass

    def _set_warning_text(self, warnings: List[str]) -> None:
        status_label = getattr(self, "updated_label", None)
        if status_label is None:
            return
        base_text = str(getattr(self, "_status_base_text", "") or HEADER_BASE_TEXT).strip()
        if warnings:
            text = f"{base_text} • ПРЕДУПРЕЖДЕНИЯ: " + " | ".join(warnings[:2])
            if len(warnings) > 2:
                text += f" | +{len(warnings) - 2} еще"
            status_label.configure(bg=WARN_BG, fg=WARN_TEXT)
        else:
            text = base_text
            status_label.configure(bg=APP_BG, fg=TEXT_MUTED)
        status_label.configure(text=text)

    def _populate_peers(self, rows: List[Dict[str, object]]) -> None:
        current_ids = list(self.peer_tree.get_children())
        desired_ids = [str(row.get("public_key", "")) for row in rows]
        if current_ids == desired_ids:
            unchanged = True
            for row, iid in zip(rows, current_ids):
                diagnostic = _peer_diagnostic_state(row)
                values = (
                    "●",
                    row["name"],
                    row["vpn_ip"],
                    diagnostic["label"],
                    row["endpoint_location"],
                    row["channel"],
                    row["handshake"],
                    row["rx"],
                    row["tx"],
                    row["current"],
                    row["share"],
                    row["today"],
                    row["total"],
                )
                if tuple(self.peer_tree.item(iid, "values")) != values:
                    unchanged = False
                    break
            if unchanged:
                self.table_status_label.configure(text=f"{len(rows)} peers" if rows else "0 peers")
                return

        if current_ids:
            self.peer_tree.delete(*current_ids)
        for row in rows:
            diagnostic = _peer_diagnostic_state(row)
            base_tag = "active" if bool(row.get("active_value")) else "inactive"
            tags = (base_tag, f"issue_{diagnostic['key']}")
            self.peer_tree.insert(
                "",
                "end",
                iid=str(row.get("public_key", "")),
                tags=tags,
                values=(
                    "●",
                    row["name"],
                    row["vpn_ip"],
                    diagnostic["label"],
                    row["endpoint_location"],
                    row["channel"],
                    row["handshake"],
                    row["rx"],
                    row["tx"],
                    row["current"],
                    row["share"],
                    row["today"],
                    row["total"],
                ),
            )
        if rows:
            self.table_status_label.configure(text=f"{len(rows)} peers")
        else:
            self.table_status_label.configure(text="0 peers")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_refresh_timer()
        self._cancel_refresh_result_poll()
        if self._ssh_process is not None and self._ssh_process.poll() is None:
            try:
                self._ssh_process.terminate()
            except Exception:  # pragma: no cover - cleanup best effort
                pass
        self._stop_remote_monitoring()
        if self._status_blink_job is not None:
            try:
                self.root.after_cancel(self._status_blink_job)
            except Exception:
                pass
        self.root.destroy()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autostop VPN desktop shell")
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host name")
    parser.add_argument("--ssh-user", default=DEFAULT_SSH_USER, help="SSH user")
    parser.add_argument("--key-path", default="", help="SSH private key path")
    parser.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT, help="Local tunnel port")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT, help="Remote dashboard port")
    parser.add_argument("--refresh-seconds", type=float, default=DEFAULT_REFRESH_SECONDS, help="Auto-refresh interval in seconds")
    parser.add_argument(
        "--no-manage-remote-monitoring",
        action="store_false",
        dest="manage_remote_monitoring",
        default=True,
        help="Do not start/stop server monitoring services with the desktop shell",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    if tk is None or ttk is None:  # pragma: no cover - depends on local Windows install
        raise SystemExit("tkinter is required for the Autostop VPN shell")

    args = parse_args(argv)
    instance_lock = _acquire_single_instance_lock()
    if os.name == "nt" and instance_lock is None:
        _focus_existing_window()
        return 0
    root = tk.Tk()
    ShellApp(
        root,
        host=args.host,
        ssh_user=args.ssh_user,
        key_path=_resolve_key_path(args.key_path),
        local_port=args.local_port,
        remote_port=args.remote_port,
        refresh_seconds=args.refresh_seconds,
        manage_remote_monitoring=args.manage_remote_monitoring,
    )
    try:
        root.mainloop()
    finally:
        if instance_lock is not None:
            ctypes.windll.kernel32.CloseHandle(instance_lock)  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
