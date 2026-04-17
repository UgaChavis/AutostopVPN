from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys_module = __import__("sys")
for path in (str(SCRIPTS), str(ROOT)):
    if path not in sys_module.path:
        sys_module.path.insert(0, path)

import amnezia_traffic_collector as collector


class AmneziaTrafficCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_time = datetime(2026, 4, 17, 12, 30, tzinfo=timezone.utc)

    def _patch_repo_paths(self, stack: ExitStack, base_dir: Path) -> None:
        data_dir = base_dir / "data"
        stack.enter_context(patch.object(collector, "DATA_DIR", data_dir))
        stack.enter_context(patch.object(collector, "STATE_FILE", data_dir / "state.json"))
        stack.enter_context(patch.object(collector, "TOTALS_FILE", data_dir / "totals.json"))
        stack.enter_context(patch.object(collector, "SUMMARY_FILE", data_dir / "summary.json"))
        stack.enter_context(patch.object(collector, "DAILY_DIR", data_dir / "daily"))
        stack.enter_context(patch.object(collector, "REPORTS_DIR", data_dir / "reports"))
        stack.enter_context(patch.object(collector, "WEB_DIR", data_dir / "web"))
        stack.enter_context(patch.object(collector, "ALIASES_FILE", data_dir / "aliases.csv"))
        stack.enter_context(patch.object(collector, "SERVER_INFO_FILE", data_dir / "server_info.json"))
        stack.enter_context(patch.object(collector, "WEB_SUMMARY_FILE", data_dir / "web" / "dashboard.json"))
        stack.enter_context(patch.object(collector, "WEB_INDEX_FILE", data_dir / "web" / "index.html"))

    def test_build_peer_rows_accumulates_deltas_and_marks_active(self) -> None:
        peer = {
            "public_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF",
            "endpoint": "198.51.100.10:51234",
            "allowed_ips": "10.0.0.2/32",
            "vpn_ip": "10.0.0.2",
            "latest_handshake": int(self.current_time.timestamp()) - 45,
            "rx_bytes": 300,
            "tx_bytes": 700,
        }
        state = {
            "container_id": "container-123",
            "started_at": "2026-04-17T11:00:00+00:00",
            "sampled_at": "2026-04-17T12:29:30+00:00",
            "peers": {
                peer["public_key"]: {
                    "rx_bytes": 100,
                    "tx_bytes": 200,
                    "vpn_ip": "10.0.0.2",
                    "allowed_ips": "10.0.0.2/32",
                }
            },
        }
        totals = {
            "timezone": "UTC",
            "container": "amnezia-awg2",
            "interface": "awg0",
            "updated_at": None,
            "peers": {
                peer["public_key"]: {
                    "public_key": peer["public_key"],
                    "vpn_ip": "10.0.0.2",
                    "allowed_ips": "10.0.0.2/32",
                    "name": "",
                    "total_rx_bytes": 1000,
                    "total_tx_bytes": 2000,
                    "first_seen_at": "2026-04-17T11:00:00+00:00",
                    "last_seen_at": "2026-04-17T12:29:30+00:00",
                    "last_endpoint": "198.51.100.10:51234",
                    "last_handshake": int(self.current_time.timestamp()) - 45,
                }
            },
        }
        aliases = {peer["public_key"]: {"name": "VPN Client", "vpn_ip": "10.0.0.2"}}
        meta = {"container_id": "container-123", "started_at": "2026-04-17T11:00:00+00:00", "status": "running", "image": "amnezia"}

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            self._patch_repo_paths(stack, Path(temp_dir))
            rows, daily_data, new_state = collector.build_peer_rows(
                peers=[peer],
                totals=totals,
                aliases=aliases,
                state=state,
                meta=meta,
                current_time=self.current_time,
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "VPN Client")
        self.assertTrue(row["is_active"])
        self.assertEqual(row["today_rx_bytes"], 200)
        self.assertEqual(row["today_tx_bytes"], 500)
        self.assertEqual(row["total_rx_bytes"], 1200)
        self.assertEqual(row["total_tx_bytes"], 2500)
        self.assertAlmostEqual(row["current_rx_bps"], 200 / 30, places=6)
        self.assertAlmostEqual(row["current_tx_bps"], 500 / 30, places=6)
        self.assertEqual(row["current_share_percent"], 100.0)
        self.assertEqual(daily_data["peers"][peer["public_key"]]["rx_bytes"], 200)
        self.assertEqual(daily_data["peers"][peer["public_key"]]["tx_bytes"], 500)
        self.assertEqual(new_state["container_id"], "container-123")
        self.assertEqual(new_state["peers"][peer["public_key"]]["rx_bytes"], 300)
        self.assertEqual(new_state["peers"][peer["public_key"]]["tx_bytes"], 700)
        self.assertEqual(totals["updated_at"], self.current_time.isoformat())

    def test_get_bandwidth_capacity_prefers_configured_limit(self) -> None:
        capacity = collector.get_bandwidth_capacity(
            {
                "bandwidth_limit_mbps": 1000,
                "network_interface": "eth0",
            }
        )

        self.assertEqual(capacity["capacity_bytes_per_sec"], 125000000)
        self.assertEqual(capacity["capacity_mbps"], 1000.0)
        self.assertEqual(capacity["source"], "server_info.json:bandwidth_limit_mbps")

    def test_collect_writes_summary_and_dashboard_outputs(self) -> None:
        peer = {
            "public_key": "ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210abcd",
            "endpoint": "203.0.113.20:51820",
            "allowed_ips": "10.0.0.3/32",
            "vpn_ip": "10.0.0.3",
            "latest_handshake": int(self.current_time.timestamp()) - 60,
            "rx_bytes": 2048,
            "tx_bytes": 1024,
        }
        container_meta = {
            "container_id": "container-456",
            "started_at": "2026-04-17T10:00:00+00:00",
            "status": "running",
            "image": "amnezia-awg:latest",
        }
        interface_meta = {"listen_port": 47895}
        state = {
            "container_id": "container-456",
            "started_at": "2026-04-17T10:00:00+00:00",
            "sampled_at": "2026-04-17T12:29:30+00:00",
            "peers": {
                peer["public_key"]: {
                    "rx_bytes": 1024,
                    "tx_bytes": 512,
                    "vpn_ip": "10.0.0.3",
                    "allowed_ips": "10.0.0.3/32",
                }
            },
        }
        server_status = {
            "checked_at": self.current_time.isoformat(),
            "loadavg": {"1m": 0.1, "5m": 0.2, "15m": 0.3},
            "memory": {
                "total_bytes": 8 * 1024 * 1024 * 1024,
                "available_bytes": 4 * 1024 * 1024 * 1024,
                "used_bytes": 4 * 1024 * 1024 * 1024,
                "used_percent": 50.0,
            },
            "disk_root": {
                "total_bytes": 100 * 1024 * 1024 * 1024,
                "used_bytes": 20 * 1024 * 1024 * 1024,
                "free_bytes": 80 * 1024 * 1024 * 1024,
                "used_percent": 20.0,
            },
            "uptime_seconds": 86400,
            "ping": {
                "target": "1.1.1.1",
                "ok": True,
                "packet_loss_percent": None,
                "latency_min_ms": 8.0,
                "latency_avg_ms": 9.5,
                "latency_max_ms": 12.0,
                "latency_mdev_ms": 1.0,
            },
            "bandwidth": {
                "capacity_bytes_per_sec": 125000000,
                "capacity_mbps": 1000.0,
                "source": "server_info.json:bandwidth_limit_mbps",
                "interface": "eth0",
                "current_bytes_per_sec": 12345,
                "utilization_percent": 0.01,
                "headroom_bytes_per_sec": 124987655,
                "over_capacity_bytes_per_sec": 0,
            },
        }
        server_info = {
            "title": "Данные сервера",
            "server_role": "VPN и мониторинг",
            "provider_name": "Mnogoweb",
            "provider_site": "https://mnogoweb.in",
            "purchase_note": "note",
            "payment_note": "note",
            "billing_url": "",
            "billing_login_hint": "",
            "public_ip": "46.8.254.243",
            "hostname": "vps26457.mnogoweb.in",
            "domain": "crm.autostopcrm.ru",
            "ssh_user": "root",
            "ssh_port": 22,
            "os": "Ubuntu 24.04 LTS",
            "project_path": "/opt/autostopcrm",
            "vpn_container": "amnezia-awg2",
            "bandwidth_limit_mbps": 1000,
            "network_interface": "eth0",
            "notes": ["Панель доступна только локально через SSH-туннель."],
        }

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            base_dir = Path(temp_dir)
            self._patch_repo_paths(stack, base_dir)
            stack.enter_context(patch.object(collector, "now_local", return_value=self.current_time))
            stack.enter_context(patch.object(collector, "inspect_container", return_value=container_meta))
            stack.enter_context(patch.object(collector, "get_wg_dump", return_value=(interface_meta, [peer])))
            (base_dir / "data").mkdir(parents=True, exist_ok=True)
            (base_dir / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            stack.enter_context(patch.object(collector, "get_server_status", return_value=server_status))
            stack.enter_context(patch.object(collector, "load_server_info", return_value=server_info))

            result = collector.collect()

            summary_path = base_dir / "data" / "summary.json"
            web_json_path = base_dir / "data" / "web" / "dashboard.json"
            html_path = base_dir / "data" / "web" / "index.html"
            report_path = base_dir / "data" / "reports" / "current_users.csv"

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            web_summary = json.loads(web_json_path.read_text(encoding="utf-8"))
            html = html_path.read_text(encoding="utf-8")
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertEqual(summary["vpn"]["total_peers"], 1)
        self.assertEqual(summary["vpn"]["active_connections"], 1)
        self.assertGreater(summary["vpn"]["current_total_bps"], 0)
        self.assertEqual(summary["peers"][0]["current_share_percent"], 100.0)
        self.assertIn("bandwidth", summary["server"])
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(web_summary["vpn"]["listen_port"], 47895)
        self.assertIn("Панель Amnezia VPN", html)
        self.assertIn("current_share_percent", report)
        self.assertIn("vpn_ip", report)

    def test_status_prints_cached_summary(self) -> None:
        summary = {
            "updated_at": self.current_time.isoformat(),
            "timezone": "UTC",
            "container": {
                "name": "amnezia-awg2",
                "status": "running",
                "started_at": "2026-04-17T10:00:00+00:00",
                "image": "amnezia-awg:latest",
            },
            "vpn": {
                "type": "AmneziaWG",
                "interface": "awg0",
                "listen_port": 47895,
                "total_peers": 2,
                "active_connections": 1,
                "current_rx_bps": 2048,
                "current_tx_bps": 1024,
                "current_total_bps": 3072,
                "active_window_seconds": 180,
                "sample_window_seconds": 60,
            },
            "server": {
                "loadavg": {"1m": 0.5, "5m": 0.4, "15m": 0.3},
                "memory": {"used_percent": 42.0},
                "disk_root": {"used_percent": 55.0},
                "uptime_seconds": 3661,
                "ping": {"target": "1.1.1.1", "packet_loss_percent": None, "latency_avg_ms": 8.2},
                "bandwidth": {
                    "capacity_bytes_per_sec": 125000000,
                    "capacity_mbps": 1000.0,
                    "source": "server_info.json:bandwidth_limit_mbps",
                    "interface": "eth0",
                    "current_bytes_per_sec": 3072,
                    "utilization_percent": 0.0,
                    "headroom_bytes_per_sec": 124996928,
                    "over_capacity_bytes_per_sec": 0,
                },
            },
            "server_info": {"notes": []},
            "periods": {},
            "peers": [
                {
                    "name": "Peer A",
                    "vpn_ip": "10.0.0.2",
                    "current_total_bps": 1024,
                    "current_share_percent": 100.0,
                    "is_active": True,
                }
            ],
            "warnings": ["Нет активных handshake в текущем окне активности."],
        }

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            base_dir = Path(temp_dir)
            data_dir = base_dir / "data"
            stack.enter_context(patch.object(collector, "SUMMARY_FILE", data_dir / "summary.json"))
            stack.enter_context(patch.object(collector, "TOTALS_FILE", data_dir / "totals.json"))
            stack.enter_context(patch.object(collector, "ensure_dirs", return_value=None))
            (data_dir).mkdir(parents=True, exist_ok=True)
            (data_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                result = collector.status()

        output = buffer.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Контейнер: amnezia-awg2 [running]", output)
        self.assertIn("VPN: AmneziaWG / awg0 port=47895 peers=2 active=1 window=60s flow=", output)
        self.assertIn("Bandwidth: current=", output)
        self.assertIn("Warnings: 1", output)
        self.assertIn("Peer A", output)

    def test_doctor_reports_ready_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            stack.enter_context(patch.object(collector, "ensure_dirs", return_value=None))
            stack.enter_context(patch.object(collector, "SUMMARY_FILE", data_dir / "summary.json"))
            stack.enter_context(patch.object(collector.shutil, "which", side_effect=lambda name: f"C:\\bin\\{name}.exe"))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                result = collector.doctor()

        output = buffer.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("[OK] docker", output)
        self.assertIn("[OK] ping", output)
        self.assertIn("[OK] ip", output)
        self.assertIn("[OK] dashboard launcher", output)
        self.assertIn("[WARN] summary cache", output)


if __name__ == "__main__":
    unittest.main()
