#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import subprocess
import socket
from pathlib import Path
from typing import Dict, List, Optional
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

import amnezia_traffic_collector as collector


DEFAULT_REFRESH_SECONDS = 5
DEFAULT_LOCAL_PORT = 18765
DEFAULT_REMOTE_PORT = 18080
DEFAULT_HOST = "46.8.254.243"
DEFAULT_SSH_USER = "root"
REQUEST_TIMEOUT_SECONDS = 3.0
_SINGLE_INSTANCE_MUTEX_NAME = "Global\\AutostopVPNShell"


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


def _format_storage(used_bytes: object, total_bytes: object, used_percent: object) -> str:
    used_label = collector.format_bytes(_coerce_int(used_bytes))
    total_label = collector.format_bytes(_coerce_int(total_bytes))
    percent_label = "н/д" if used_percent is None else f"{_coerce_float(used_percent):.2f}%"
    return f"{percent_label} ({used_label}/{total_label})"


def _format_snapshot_age(updated_at: object) -> str:
    parsed = collector.parse_iso_datetime(str(updated_at)) if updated_at else None
    if parsed is None:
        return "н/д"
    age_seconds = max(int((collector.now_local() - parsed).total_seconds()), 0)
    return collector.format_age(age_seconds)


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


def _resolve_ssh_executable() -> str:
    for candidate in ("ssh.exe", "ssh"):
        command = shutil.which(candidate)
        if command:
            return command
    raise FileNotFoundError("ssh executable not found")


def _resolve_key_path(explicit_key_path: str = "") -> str:
    if explicit_key_path:
        candidate = Path(explicit_key_path)
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(f"SSH key not found: {candidate}")

    candidates = [
        Path.home() / ".ssh" / "autostopvpn_server_ed25519",
        Path.home() / ".ssh" / "autostopcrm_server_ed25519",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("SSH key not found. Checked autostopvpn_server_ed25519 and autostopcrm_server_ed25519.")


def _start_ssh_tunnel(host: str, user: str, key_path: str, local_port: int, remote_port: int) -> subprocess.Popen[str]:
    ssh_executable = _resolve_ssh_executable()
    tunnel_spec = f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.Popen(
        [
            ssh_executable,
            "-i",
            key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-N",
            "-L",
            tunnel_spec,
            f"{user}@{host}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def fetch_summary(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> Dict[str, object]:
    request = Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    summary = json.loads(payload)
    if not isinstance(summary, dict):
        raise ValueError("Dashboard payload must be a JSON object")
    return summary


def build_view_model(summary: Dict[str, object], source_url: str, refresh_seconds: int) -> Dict[str, object]:
    container = summary.get("container", {}) if isinstance(summary.get("container", {}), dict) else {}
    vpn = summary.get("vpn", {}) if isinstance(summary.get("vpn", {}), dict) else {}
    server = summary.get("server", {}) if isinstance(summary.get("server", {}), dict) else {}
    bandwidth = server.get("bandwidth", {}) if isinstance(server.get("bandwidth", {}), dict) else {}
    daily = bandwidth.get("daily", {}) if isinstance(bandwidth.get("daily", {}), dict) else {}
    loadavg = server.get("loadavg", {}) if isinstance(server.get("loadavg", {}), dict) else {}
    memory = server.get("memory", {}) if isinstance(server.get("memory", {}), dict) else {}
    disk = server.get("disk_root", {}) if isinstance(server.get("disk_root", {}), dict) else {}
    ping = server.get("ping", {}) if isinstance(server.get("ping", {}), dict) else {}
    warnings = [str(item) for item in summary.get("warnings", []) if str(item).strip()]

    capacity_bps = _coerce_int(bandwidth.get("capacity_bytes_per_sec"))
    current_total_bps = _coerce_int(vpn.get("current_total_bps"))
    utilization_percent = bandwidth.get("utilization_percent")
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
        peer_rows.append(
            {
                "name": str(peer.get("name", "")),
                "vpn_ip": str(peer.get("vpn_ip", "")),
                "active": "да" if bool(peer.get("is_active")) else "нет",
                "active_value": bool(peer.get("is_active")),
                "handshake": collector.format_age(peer.get("handshake_age_seconds")),
                "current": collector.format_rate(current_bps),
                "current_bps": current_bps,
                "share": collector.format_percent(peer.get("current_share_percent")),
                "share_value": _coerce_float(peer.get("current_share_percent")),
                "today": collector.format_bytes(_coerce_int(peer.get("today_bytes"))),
                "today_bytes": _coerce_int(peer.get("today_bytes")),
                "total": collector.format_bytes(total_bytes),
                "total_bytes": total_bytes,
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

    return {
        "source_url": source_url,
        "refresh_seconds": refresh_seconds,
        "updated_label": collector.format_timestamp(summary.get("updated_at")),
        "age_label": _format_snapshot_age(summary.get("updated_at")),
        "container_label": f"{container.get('name', '')} [{collector.translate_container_status(str(container.get('status', '')))}]",
        "container_image": str(container.get("image", "")),
        "vpn_label": f"{vpn.get('type', 'VPN')} / {vpn.get('interface', '')}",
        "listen_port": _coerce_int(vpn.get("listen_port")),
        "total_peers": _coerce_int(vpn.get("total_peers")),
        "active_connections": _coerce_int(vpn.get("active_connections")),
        "current_rx": collector.format_rate(_coerce_int(vpn.get("current_rx_bps"))),
        "current_tx": collector.format_rate(_coerce_int(vpn.get("current_tx_bps"))),
        "current_total": collector.format_rate(current_total_bps),
        "current_total_bps": current_total_bps,
        "bandwidth_limit": bandwidth_limit,
        "bandwidth_utilization": bandwidth_utilization,
        "bandwidth_headroom": bandwidth_headroom,
        "bandwidth_state_class": bandwidth_state["class"],
        "bandwidth_state_label": bandwidth_state["label"],
        "bandwidth_state_note": bandwidth_state["note"],
        "bandwidth_bar_width_percent": float(bandwidth_state["bar_width_percent"]),
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
        "server_ping": _format_latency(ping.get("latency_avg_ms")),
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
        refresh_seconds: int,
        dashboard_url: Optional[str] = None,
    ) -> None:
        self.root = root
        self.host = host
        self.ssh_user = ssh_user
        self.key_path = key_path
        self.local_port = local_port
        self.remote_port = remote_port
        self.dashboard_url = dashboard_url or f"http://127.0.0.1:{local_port}/dashboard.json"
        self.refresh_seconds = max(refresh_seconds, 1)
        self.refresh_ms = self.refresh_seconds * 1000
        self._refresh_after_id: Optional[str] = None
        self._refresh_in_flight = False
        self._closed = False
        self._last_model: Optional[Dict[str, object]] = None
        self._ssh_process: Optional[subprocess.Popen[bytes]] = None

        self.root.title("Autostop VPN Shell")
        self.root.geometry("1200x780")
        self.root.minsize(1080, 680)

        self._build_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<F5>", self._on_refresh_key)
        self.root.bind_all("<Control-r>", self._on_refresh_key)
        self.root.bind_all("<Control-R>", self._on_refresh_key)

        self.request_refresh()

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - theme availability varies
            pass
        style.configure("Shell.TFrame", background="#f6f6f2")
        style.configure("Shell.TLabel", background="#f6f6f2", foreground="#111111", font=("Consolas", 10))
        style.configure("ShellTitle.TLabel", background="#f6f6f2", foreground="#111111", font=("Consolas", 15, "bold"))
        style.configure("ShellSection.TLabelframe", background="#f6f6f2", foreground="#111111")
        style.configure("ShellSection.TLabelframe.Label", background="#f6f6f2", foreground="#111111", font=("Consolas", 10, "bold"))
        style.configure("ShellValue.TLabel", background="#f6f6f2", foreground="#111111", font=("Consolas", 11, "bold"))
        style.configure("ShellSmall.TLabel", background="#f6f6f2", foreground="#444444", font=("Consolas", 9))
        style.configure("Shell.Treeview", font=("Consolas", 9), rowheight=22)
        style.configure("Shell.Treeview.Heading", font=("Consolas", 9, "bold"))

    def _build_layout(self) -> None:
        root = self.root
        root.configure(background="#f6f6f2")

        container = ttk.Frame(root, style="Shell.TFrame", padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        header = ttk.Frame(container, style="Shell.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Autostop VPN Shell", style="ShellTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.state_label = ttk.Label(header, text="Ожидание данных...", style="ShellValue.TLabel")
        self.state_label.grid(row=0, column=1, sticky="e")
        self.substate_label = ttk.Label(header, text="", style="ShellSmall.TLabel")
        self.substate_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        controls = ttk.Frame(container, style="Shell.TFrame")
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(0, weight=1)
        self.updated_label = ttk.Label(controls, text="", style="ShellSmall.TLabel")
        self.updated_label.grid(row=0, column=0, sticky="w")
        refresh_button = ttk.Button(controls, text="Обновить", command=self.request_refresh)
        refresh_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        close_button = ttk.Button(controls, text="Закрыть", command=self.close)
        close_button.grid(row=0, column=2, sticky="e", padx=(8, 0))

        metrics = ttk.Frame(container, style="Shell.TFrame")
        metrics.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        metrics.columnconfigure(0, weight=1)
        metrics.columnconfigure(1, weight=1)
        metrics.columnconfigure(2, weight=1)

        self.channel_box = self._create_channel_box(metrics)
        self.channel_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.vpn_box = self._create_vpn_box(metrics)
        self.vpn_box.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.server_box = self._create_server_box(metrics)
        self.server_box.grid(row=0, column=2, sticky="nsew")

        warning_frame = ttk.LabelFrame(container, text="Предупреждения", style="ShellSection.TLabelframe", padding=10)
        warning_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        warning_frame.columnconfigure(0, weight=1)
        self.warning_text = tk.Text(
            warning_frame,
            height=4,
            wrap="word",
            borderwidth=0,
            background="#f6f6f2",
            foreground="#111111",
            font=("Consolas", 9),
            relief="flat",
        )
        self.warning_text.grid(row=0, column=0, sticky="ew")
        self.warning_text.configure(state="disabled")

        peers_frame = ttk.LabelFrame(container, text="Пиры", style="ShellSection.TLabelframe", padding=10)
        peers_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        peers_frame.columnconfigure(0, weight=1)
        peers_frame.rowconfigure(0, weight=1)

        columns = ("name", "vpn_ip", "active", "current", "share", "today", "total", "handshake")
        self.peer_tree = ttk.Treeview(peers_frame, columns=columns, show="headings", style="Shell.Treeview")
        headings = {
            "name": "Имя",
            "vpn_ip": "VPN IP",
            "active": "Активен",
            "current": "Текущая",
            "share": "Доля",
            "today": "Сегодня",
            "total": "Всего",
            "handshake": "Handshake",
        }
        widths = {
            "name": 180,
            "vpn_ip": 110,
            "active": 70,
            "current": 120,
            "share": 80,
            "today": 120,
            "total": 120,
            "handshake": 100,
        }
        for column in columns:
            self.peer_tree.heading(column, text=headings[column])
            self.peer_tree.column(column, width=widths[column], anchor="w", stretch=column in {"name", "current", "today", "total"})
        peer_scroll = ttk.Scrollbar(peers_frame, orient="vertical", command=self.peer_tree.yview)
        self.peer_tree.configure(yscrollcommand=peer_scroll.set)
        self.peer_tree.grid(row=0, column=0, sticky="nsew")
        peer_scroll.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(container, style="Shell.TFrame")
        footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.footer_label = ttk.Label(footer, text="", style="ShellSmall.TLabel")
        self.footer_label.grid(row=0, column=0, sticky="w")
        self.connection_label = ttk.Label(footer, text="", style="ShellSmall.TLabel")
        self.connection_label.grid(row=0, column=1, sticky="e")

    def _create_channel_box(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Канал", style="ShellSection.TLabelframe", padding=10)
        frame.columnconfigure(0, weight=1)
        self.channel_current_label = ttk.Label(frame, text="", style="ShellValue.TLabel")
        self.channel_current_label.grid(row=0, column=0, sticky="w")
        self.channel_info_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.channel_info_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.channel_state_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.channel_state_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.channel_bar = tk.Canvas(
            frame,
            height=24,
            background="#f6f6f2",
            highlightthickness=0,
            borderwidth=0,
        )
        self.channel_bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.channel_bar.bind("<Configure>", self._redraw_channel_bar)
        self.channel_day_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.channel_day_label.grid(row=4, column=0, sticky="w", pady=(6, 0))
        return frame

    def _create_vpn_box(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="VPN", style="ShellSection.TLabelframe", padding=10)
        frame.columnconfigure(0, weight=1)
        self.vpn_summary_label = ttk.Label(frame, text="", style="ShellValue.TLabel")
        self.vpn_summary_label.grid(row=0, column=0, sticky="w")
        self.vpn_flow_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.vpn_flow_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.vpn_container_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.vpn_container_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.vpn_image_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.vpn_image_label.grid(row=3, column=0, sticky="w", pady=(2, 0))
        return frame

    def _create_server_box(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Сервер", style="ShellSection.TLabelframe", padding=10)
        frame.columnconfigure(0, weight=1)
        self.server_load_label = ttk.Label(frame, text="", style="ShellValue.TLabel")
        self.server_load_label.grid(row=0, column=0, sticky="w")
        self.server_memory_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.server_memory_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.server_disk_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.server_disk_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.server_ping_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.server_ping_label.grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.server_uptime_label = ttk.Label(frame, text="", style="ShellSmall.TLabel")
        self.server_uptime_label.grid(row=4, column=0, sticky="w", pady=(2, 0))
        return frame

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

    def _port_in_use(self) -> bool:
        return _test_local_port(self.local_port)

    def _ensure_tunnel(self) -> None:
        if self._closed:
            return
        if self._port_in_use():
            return
        if self._ssh_process is not None and self._ssh_process.poll() is not None:
            self._ssh_process = None
        if self._ssh_process is None:
            self._ssh_process = _start_ssh_tunnel(self.host, self.ssh_user, self.key_path, self.local_port, self.remote_port)
        for _ in range(40):
            if self._closed:
                return
            if self._port_in_use():
                return
            if self._ssh_process is not None and self._ssh_process.poll() is not None:
                raise RuntimeError(f"SSH tunnel exited with code {self._ssh_process.returncode}")
            time.sleep(0.25)
        raise TimeoutError(f"Tunnel did not open on 127.0.0.1:{self.local_port}")

    def request_refresh(self) -> None:
        if self._closed or self._refresh_in_flight:
            return
        self._cancel_refresh_timer()
        self._refresh_in_flight = True
        self.state_label.configure(text="Обновление...")
        self.substate_label.configure(text="Запрос свежей статистики по SSH-туннелю.")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            self._ensure_tunnel()
            summary = fetch_summary(self.dashboard_url)
            model = build_view_model(summary, self.dashboard_url, self.refresh_seconds)
        except Exception as exc:  # pragma: no cover - network and runtime dependent
            self.root.after(0, lambda exc=exc: self._handle_refresh_error(exc))
            return
        self.root.after(0, lambda model=model: self._handle_refresh_success(model))

    def _handle_refresh_success(self, model: Dict[str, object]) -> None:
        if self._closed:
            return
        self._refresh_in_flight = False
        self._last_model = model

        self.root.title(f"Autostop VPN Shell · {model['bandwidth_state_label']}")
        self.state_label.configure(text=model["bandwidth_state_label"])
        self.substate_label.configure(text=model["bandwidth_state_note"])
        self.updated_label.configure(
            text=f"Обновлено: {model['updated_label']} | Возраст: {model['age_label']} | Интервал: {model['refresh_seconds']} сек."
        )
        self.channel_current_label.configure(text=f"{model['current_total']} / {model['bandwidth_limit']}")
        self.channel_info_label.configure(
            text=f"Загрузка: {model['bandwidth_utilization']} | Запас: {model['bandwidth_headroom']}"
        )
        self.channel_state_label.configure(text=f"Статус канала: {model['bandwidth_state_label']}")
        self._last_model = model
        self._redraw_channel_bar()
        self.channel_day_label.configure(
            text=f"Средний поток за день: {model['daily_average']} | Пик за день: {model['daily_peak']} ({model['daily_peak_utilization']})"
        )

        self.vpn_summary_label.configure(
            text=f"{model['vpn_label']} | peers {model['active_connections']}/{model['total_peers']} | port {model['listen_port']}"
        )
        self.vpn_flow_label.configure(text=f"Текущий поток: RX {model['current_rx']} / TX {model['current_tx']}")
        self.vpn_container_label.configure(text=f"Контейнер: {model['container_label']}")
        self.vpn_image_label.configure(text=f"Image: {model['container_image']}")

        self.server_load_label.configure(text=f"Loadavg: {model['server_load']}")
        self.server_memory_label.configure(text=f"Memory: {model['server_memory']}")
        self.server_disk_label.configure(text=f"Disk /: {model['server_disk']}")
        self.server_ping_label.configure(text=f"Ping: {model['server_ping']}")
        self.server_uptime_label.configure(text=f"Uptime: {model['server_uptime']}")

        self._set_warning_text(model["warnings"])
        self._populate_peers(model["peer_rows"])
        self.footer_label.configure(text=f"Источник: {model['source_url']}")
        self.connection_label.configure(text="Соединение активно")
        self._schedule_refresh()

    def _handle_refresh_error(self, exc: Exception) -> None:
        if self._closed:
            return
        self._refresh_in_flight = False
        self.state_label.configure(text="Нет данных")
        self.substate_label.configure(text=f"Не удалось обновить данные: {exc}")
        self.connection_label.configure(text="Проблема с соединением")
        if self._last_model is None:
            self.updated_label.configure(text="Обновлено: нет данных")
            self.footer_label.configure(text=f"Источник: {self.dashboard_url}")
            self._set_warning_text([f"Ошибка загрузки: {exc}"])
            self._populate_peers([])
        self._schedule_refresh()

    def _set_warning_text(self, warnings: List[str]) -> None:
        self.warning_text.configure(state="normal")
        self.warning_text.delete("1.0", "end")
        if warnings:
            self.warning_text.insert("end", "\n".join(f"• {item}" for item in warnings))
        else:
            self.warning_text.insert("end", "Предупреждений нет.")
        self.warning_text.configure(state="disabled")

    def _populate_peers(self, rows: List[Dict[str, object]]) -> None:
        for item in self.peer_tree.get_children():
            self.peer_tree.delete(item)
        for row in rows:
            self.peer_tree.insert(
                "",
                "end",
                values=(
                    row["name"],
                    row["vpn_ip"],
                    row["active"],
                    row["current"],
                    row["share"],
                    row["today"],
                    row["total"],
                    row["handshake"],
                ),
            )

    def _redraw_channel_bar(self, _event: object = None) -> None:
        canvas = self.channel_bar
        if canvas is None:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        model = self._last_model
        state_class = str(model.get("bandwidth_state_class", "muted")) if model else "muted"
        utilization = _coerce_float(model.get("bandwidth_bar_width_percent")) if model else 0.0
        current_total_bps = _coerce_int(model.get("current_total_bps")) if model else 0
        fill_width = 0
        if model and current_total_bps > 0:
            fill_width = max(int(round(width * utilization / 100.0)), 6)
            fill_width = min(fill_width, width)

        colors = {
            "ok": ("#dfeadf", "#2f7a36", "#18351d"),
            "warn": ("#f2e1b8", "#9a6422", "#5b3a11"),
            "danger": ("#f0c0c0", "#a33c3c", "#5f1717"),
            "muted": ("#dcdcdc", "#8a8a8a", "#666666"),
        }
        track_color, fill_color, marker_color = colors.get(state_class, colors["muted"])

        margin_y = 4
        track_top = margin_y
        track_bottom = height - margin_y
        canvas.create_rectangle(0, track_top, width, track_bottom, fill=track_color, outline="#8b8b8b")
        if fill_width > 0:
            canvas.create_rectangle(0, track_top, fill_width, track_bottom, fill=fill_color, outline=fill_color)

        for fraction in (0.25, 0.5, 0.75, 1.0):
            tick_x = int(round(width * fraction))
            canvas.create_line(tick_x, track_top, tick_x, track_bottom, fill="#ffffff", width=1)

        marker_x = 0
        if model and current_total_bps > 0 and width > 0:
            marker_x = min(max(int(round(width * utilization / 100.0)), 1), width - 1)
            canvas.create_line(marker_x, track_top - 2, marker_x, track_bottom + 2, fill=marker_color, width=2)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_refresh_timer()
        if self._ssh_process is not None and self._ssh_process.poll() is None:
            try:
                self._ssh_process.terminate()
            except Exception:  # pragma: no cover - cleanup best effort
                pass
        self.root.destroy()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autostop VPN desktop shell")
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host name")
    parser.add_argument("--ssh-user", default=DEFAULT_SSH_USER, help="SSH user")
    parser.add_argument("--key-path", default="", help="SSH private key path")
    parser.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT, help="Local tunnel port")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT, help="Remote dashboard port")
    parser.add_argument("--refresh-seconds", type=int, default=DEFAULT_REFRESH_SECONDS, help="Auto-refresh interval")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    if tk is None or ttk is None:  # pragma: no cover - depends on local Windows install
        raise SystemExit("tkinter is required for the Autostop VPN shell")

    args = parse_args(argv)
    instance_lock = _acquire_single_instance_lock()
    if os.name == "nt" and instance_lock is None:
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
    )
    try:
        root.mainloop()
    finally:
        if instance_lock is not None:
            ctypes.windll.kernel32.CloseHandle(instance_lock)  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
