from __future__ import annotations

import queue
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import amnezia_vpn_shell as shell


class _DummyRoot:
    def __init__(self) -> None:
        self.after_calls = []

    def after(self, delay_ms, callback):
        self.after_calls.append((delay_ms, callback))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, _after_id):
        return None


class _DummyLabel:
    def __init__(self) -> None:
        self.values = []

    def configure(self, **kwargs):
        self.values.append(kwargs)


class _DummyHttpResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class AmneziaVpnShellTests(unittest.TestCase):
    def test_fetch_summary_retries_transient_connection_reset(self) -> None:
        response = _DummyHttpResponse(b'{"container": {"status": "running"}}')
        with patch.object(shell, "FETCH_RETRY_DELAYS_SECONDS", (0.0,)):
            with patch.object(shell, "urlopen", side_effect=[ConnectionResetError("reset"), response]) as urlopen_mock:
                summary = shell.fetch_summary("http://127.0.0.1:18765/dashboard.json")

        self.assertEqual(summary["container"]["status"], "running")
        self.assertEqual(urlopen_mock.call_count, 2)

    def test_build_view_model_orders_peers_and_formats_channel_state(self) -> None:
        summary = {
            "updated_at": "2026-04-17T12:30:00+00:00",
            "container": {
                "name": "amnezia-awg2",
                "status": "running",
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
            },
            "server": {
                "loadavg": {"1m": 0.2, "5m": 0.3, "15m": 0.4},
                "memory": {
                    "used_bytes": 1024,
                    "total_bytes": 4096,
                    "used_percent": 25.0,
                },
                "disk_root": {
                    "used_bytes": 2048,
                    "total_bytes": 8192,
                    "used_percent": 25.0,
                },
                "uptime_seconds": 3661,
                "ping": {
                    "latency_avg_ms": 9.5,
                },
                "bandwidth": {
                    "capacity_bytes_per_sec": 125000000,
                    "utilization_percent": 0.0,
                    "headroom_bytes_per_sec": 124996928,
                    "over_capacity_bytes_per_sec": 0,
                    "daily": {
                        "average_current_total_bps": 1024.0,
                        "peak_current_total_bps": 4096,
                        "peak_utilization_percent": 10.0,
                    },
                },
            },
            "warnings": ["first warning"],
            "peers": [
                {
                    "name": "Peer B",
                    "vpn_ip": "10.0.0.2",
                    "endpoint": "8.8.8.8:51820",
                    "endpoint_location": "Dallas, US",
                    "public_key": "peer-b-key",
                    "public_key_short": "peer-b",
                    "is_active": False,
                    "handshake_age_seconds": 180,
                    "current_total_bps": 1024,
                    "current_share_percent": 33.33,
                    "today_bytes": 1024,
                    "total_bytes": 2048,
                },
                {
                    "name": "Peer A",
                    "vpn_ip": "10.0.0.1",
                    "endpoint": "1.1.1.1:51820",
                    "endpoint_location": "New York, US",
                    "public_key": "peer-a-key",
                    "public_key_short": "peer-a",
                    "is_active": True,
                    "handshake_age_seconds": 20,
                    "current_total_bps": 2048,
                    "current_share_percent": 66.67,
                    "today_bytes": 2048,
                    "total_bytes": 4096,
                },
            ],
        }

        model = shell.build_view_model(summary, "http://127.0.0.1:18765/dashboard.json", 1.0)

        self.assertEqual(model["bandwidth_state_label"], "норма")
        self.assertEqual(model["container_label"], "amnezia-awg2 [работает]")
        self.assertEqual(model["current_total"], "3.00 KiB/s")
        self.assertEqual(model["server_load"], "0.20 / 0.30 / 0.40")
        self.assertEqual(model["server_memory"], "25.00% (1.00 KiB/4.00 KiB)")
        self.assertEqual(model["server_disk"], "25.00% (2.00 KiB/8.00 KiB)")
        self.assertEqual(model["server_ping"], "9.50 ms")
        self.assertEqual(model["peer_rows"][0]["name"], "Peer A")
        self.assertEqual(model["peer_rows"][0]["endpoint_location"], "New York, US")
        self.assertEqual(model["peer_rows"][0]["location_bucket"], "New York")
        self.assertEqual(model["peer_rows"][0]["channel"], "awg0")
        self.assertEqual(model["offline_connections"], 1)
        self.assertEqual(model["active_connections_label"], "1 (50.0%)")
        self.assertEqual(model["offline_connections_label"], "1 (50.0%)")
        self.assertEqual(model["peer_rows"][0]["public_key_short"], "peer-a")
        self.assertEqual(model["peer_rows"][0]["current"], "2.00 KiB/s")
        self.assertEqual(model["peer_rows"][1]["name"], "Peer B")
        self.assertEqual(model["warnings"], ["first warning"])
        self.assertEqual(model["source_url"], "http://127.0.0.1:18765/dashboard.json")
        self.assertEqual(model["refresh_seconds"], 1.0)
        self.assertEqual(model["updated_clock"], "12:30:00")
        self.assertEqual(model["updated_day"], "2026-04-17")
        self.assertEqual(model["current_total_bps"], 3072)
        self.assertEqual(model["bandwidth_limit_bps"], 125000000)
        self.assertEqual(model["bandwidth_utilization_value"], 0.0)
        self.assertEqual(model["peer_rows"][0]["handshake_age_seconds"], 20)

    def test_format_endpoint_location_returns_local_label_for_private_ips(self) -> None:
        self.assertEqual(shell._format_endpoint_location("10.0.0.10:51820"), "локальная сеть")
        self.assertEqual(shell._format_endpoint_location(""), "нет endpoint")

    def test_format_endpoint_location_returns_host_for_public_ips_without_lookup(self) -> None:
        self.assertEqual(shell._format_endpoint_location("8.8.8.8:51820"), "8.8.8.8")

    def test_format_sync_status_text_keeps_header_contract(self) -> None:
        self.assertEqual(
            shell._format_sync_status_text("2026-04-18T03:00:00+07:00", "5s", 1.0),
            "ssh tunnel // live vpn telemetry // ops cockpit • SYNC 2026-04-18T03:00:00+07:00 | AGE 5s | STEP 1s",
        )

    def test_peer_diagnostic_state_classifies_operator_scenarios(self) -> None:
        base = {
            "active_value": True,
            "current_bps": 1024,
            "share_value": 2.0,
            "handshake_age_seconds": 5,
            "recommended_mtu": "1280 B",
            "interface_mtu": "1280 B",
        }

        self.assertEqual(shell._peer_diagnostic_state(base)["key"], "ok")
        self.assertEqual(shell._peer_diagnostic_state({**base, "share_value": 21.0})["key"], "top")
        self.assertEqual(shell._peer_diagnostic_state({**base, "handshake_age_seconds": 601})["key"], "stale")
        self.assertEqual(shell._peer_diagnostic_state({**base, "interface_mtu": "1420 B"})["key"], "mtu")
        self.assertEqual(shell._peer_diagnostic_state({**base, "active_value": False})["key"], "offline")

    def test_peer_matches_quick_filter_uses_diagnostic_state(self) -> None:
        top_peer = {
            "active_value": True,
            "current_bps": 2 * 1024 * 1024,
            "share_value": 22.0,
            "handshake_age_seconds": 3,
            "recommended_mtu": "1280 B",
            "interface_mtu": "1280 B",
        }
        offline_peer = {**top_peer, "active_value": False, "current_bps": 0, "share_value": 0.0}

        self.assertTrue(shell._peer_matches_quick_filter(top_peer, "top"))
        self.assertTrue(shell._peer_matches_quick_filter(top_peer, "active"))
        self.assertFalse(shell._peer_matches_quick_filter(top_peer, "offline"))
        self.assertTrue(shell._peer_matches_quick_filter(offline_peer, "offline"))
        self.assertTrue(shell._peer_matches_quick_filter(offline_peer, "issues"))

    def test_poll_refresh_results_dispatches_success_on_main_thread(self) -> None:
        app = shell.ShellApp.__new__(shell.ShellApp)
        app._closed = False
        app._refresh_results = queue.Queue()
        app._refresh_result_after_id = None
        app.root = _DummyRoot()
        app._refresh_in_flight = True
        calls = []

        def fake_success(model):
            calls.append(("success", model))

        def fake_error(exc):
            calls.append(("error", exc))

        app._handle_refresh_success = fake_success
        app._handle_refresh_error = fake_error
        app._refresh_results.put(("success", {"ok": True}))

        shell.ShellApp._poll_refresh_results(app)

        self.assertEqual(calls, [("success", {"ok": True})])
        self.assertEqual(app._refresh_result_after_id, "after-1")
        self.assertEqual(app.root.after_calls[0][0], 100)

    def test_refresh_worker_enqueues_error_without_touching_tk(self) -> None:
        app = shell.ShellApp.__new__(shell.ShellApp)
        app._refresh_results = queue.Queue()
        app._invalidate_tunnel_called = False

        def fake_invalidate():
            app._invalidate_tunnel_called = True

        def fake_ensure_tunnel():
            raise RuntimeError("boom")

        app._invalidate_tunnel = fake_invalidate
        app._ensure_tunnel = fake_ensure_tunnel
        app.dashboard_url = "http://127.0.0.1:18765/dashboard.json"
        app.refresh_seconds = 1.0

        shell.ShellApp._refresh_worker(app)

        kind, payload = app._refresh_results.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIsInstance(payload, RuntimeError)
        self.assertTrue(app._invalidate_tunnel_called)

    def test_refresh_success_skips_full_rerender_for_same_snapshot(self) -> None:
        app = shell.ShellApp.__new__(shell.ShellApp)
        app._closed = False
        app._refresh_in_flight = True
        app._last_model = {"existing": True}
        app._last_snapshot_label = "2026-04-18T03:00:00+07:00"
        app._selected_peer_key = None
        app._all_peer_rows = [{"public_key": "one"}]
        app._peer_rows_by_key = {}
        app._filtered_peer_rows = [{"public_key": "one"}]
        app._card_value_labels = {"snapshot": _DummyLabel()}
        app._card_note_labels = {"snapshot": _DummyLabel()}
        app.updated_label = _DummyLabel()
        app.connection_label = _DummyLabel()
        app.footer_label = _DummyLabel()
        app.state_label = _DummyLabel()
        app._schedule_refresh_calls = 0

        def fake_schedule_refresh():
            app._schedule_refresh_calls += 1

        def fail(*_args, **_kwargs):
            raise AssertionError("heavy render path should not run for repeated snapshot")

        app._schedule_refresh = fake_schedule_refresh
        app._apply_peer_filter = fail
        app._update_metric_cards = fail
        app._set_warning_text = lambda *_args, **_kwargs: None
        app._select_peer = fail

        shell.ShellApp._handle_refresh_success(
            app,
            {
                "updated_label": "2026-04-18T03:00:00+07:00",
                "age_label": "5s",
                "refresh_seconds": 1.0,
            },
        )

        self.assertEqual(app._refresh_in_flight, False)
        self.assertEqual(app._last_snapshot_label, "2026-04-18T03:00:00+07:00")
        self.assertEqual(app._schedule_refresh_calls, 1)
        self.assertEqual(
            app.updated_label.values[-1]["text"],
            "ssh tunnel // live vpn telemetry // ops cockpit • SYNC 2026-04-18T03:00:00+07:00 | AGE 5s | STEP 1s",
        )
        self.assertEqual(app.connection_label.values[-1]["text"], "LINK UP")

    def test_refresh_success_keeps_sync_text_after_warning_update(self) -> None:
        app = shell.ShellApp.__new__(shell.ShellApp)
        app._closed = False
        app._refresh_in_flight = True
        app._last_model = {"existing": True}
        app._last_snapshot_label = "2026-04-18T03:00:00+07:00"
        app._selected_peer_key = None
        app._all_peer_rows = [{"public_key": "one"}]
        app._peer_rows_by_key = {}
        app._filtered_peer_rows = [{"public_key": "one"}]
        app._card_value_labels = {"snapshot": _DummyLabel()}
        app._card_note_labels = {"snapshot": _DummyLabel()}
        app.updated_label = _DummyLabel()
        app.connection_label = _DummyLabel()
        app.footer_label = _DummyLabel()
        app.state_label = _DummyLabel()
        app._schedule_refresh = lambda: None
        app._apply_peer_filter = lambda: None
        app._update_metric_cards = lambda *_args, **_kwargs: None
        app._select_peer = lambda *_args, **_kwargs: None

        shell.ShellApp._handle_refresh_success(
            app,
            {
                "updated_label": "2026-04-18T03:00:00+07:00",
                "age_label": "5s",
                "refresh_seconds": 1.0,
                "warnings": [],
            },
        )

        self.assertEqual(
            app.updated_label.values[-1]["text"],
            "ssh tunnel // live vpn telemetry // ops cockpit • SYNC 2026-04-18T03:00:00+07:00 | AGE 5s | STEP 1s",
        )

    def test_update_metric_cards_shows_channel_utilization_as_large_value(self) -> None:
        summary = {
            "updated_at": "2026-04-17T12:30:00+00:00",
            "container": {"name": "amnezia-awg2", "status": "running", "image": "amnezia-awg:latest"},
            "vpn": {
                "type": "AmneziaWG",
                "interface": "awg0",
                "listen_port": 47895,
                "total_peers": 1,
                "active_connections": 1,
                "current_rx_bps": 2048,
                "current_tx_bps": 1024,
                "current_total_bps": 3072,
            },
            "server": {
                "loadavg": {"1m": 0.2, "5m": 0.3, "15m": 0.4},
                "memory": {"used_bytes": 1024, "total_bytes": 4096, "used_percent": 25.0},
                "disk_root": {"used_bytes": 2048, "total_bytes": 8192, "used_percent": 25.0},
                "uptime_seconds": 3661,
                "ping": {"latency_avg_ms": 9.5},
                "bandwidth": {
                    "capacity_bytes_per_sec": 125000000,
                    "utilization_percent": 0.0,
                    "headroom_bytes_per_sec": 124996928,
                    "over_capacity_bytes_per_sec": 0,
                    "daily": {"average_current_total_bps": 1024.0, "peak_current_total_bps": 4096, "peak_utilization_percent": 10.0},
                },
            },
            "peers": [],
            "warnings": [],
        }
        model = shell.build_view_model(summary, "http://127.0.0.1:18765/dashboard.json", 1.0)

        app = shell.ShellApp.__new__(shell.ShellApp)
        app._all_peer_rows = list(model["peer_rows"])
        app._filtered_peer_rows = list(model["peer_rows"])
        app._search_query = type("_Q", (), {"get": lambda self: ""})()
        app._card_value_labels = {
            "channel": _DummyLabel(),
            "peers": _DummyLabel(),
            "server": _DummyLabel(),
            "leader": _DummyLabel(),
            "snapshot": _DummyLabel(),
        }
        app._card_secondary_value_labels = {"channel": _DummyLabel()}
        app._card_note_labels = {
            "channel": _DummyLabel(),
            "peers": _DummyLabel(),
            "server": _DummyLabel(),
            "leader": _DummyLabel(),
            "snapshot": _DummyLabel(),
        }
        app._render_trend_graph = lambda *_args, **_kwargs: None

        shell.ShellApp._update_metric_cards(app, model)

        self.assertEqual(app._card_value_labels["channel"].values[-1]["text"], "3.00 KiB/s")
        self.assertEqual(app._card_secondary_value_labels["channel"].values[-1]["text"], "0.00%")

    def test_resolve_key_path_prefers_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "custom.key"
            key_path.write_text("dummy", encoding="utf-8")
            with patch.dict(os.environ, {"AUTOSTOPVPN_SSH_KEY": str(key_path)}, clear=False):
                self.assertEqual(shell._resolve_key_path(), str(key_path))

    def test_resolve_key_path_falls_back_to_codex_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            ssh_dir = home / ".ssh"
            ssh_dir.mkdir()
            key_path = ssh_dir / "codex_autostopcrm_key"
            key_path.write_text("dummy", encoding="utf-8")
            env_overrides = {"AUTOSTOPVPN_SSH_KEY": "", "AUTOSTOPCRM_SSH_KEY": ""}
            with patch.dict(os.environ, env_overrides, clear=False):
                with patch.object(shell.Path, "home", return_value=home):
                    self.assertEqual(shell._resolve_key_path(), str(key_path))

    def test_parse_args_manages_remote_monitoring_by_default(self) -> None:
        self.assertTrue(shell.parse_args([]).manage_remote_monitoring)
        self.assertFalse(shell.parse_args(["--no-manage-remote-monitoring"]).manage_remote_monitoring)

    def test_remote_monitoring_control_uses_start_and_stop_scripts(self) -> None:
        calls = []

        def fake_run(_host, _user, _key_path, remote_command, _timeout=shell._SSH_SERVICE_TIMEOUT_SECONDS):
            calls.append(remote_command)

        with patch.object(shell, "_run_ssh_remote_command", side_effect=fake_run):
            shell._run_remote_monitoring_control("vpn.example", "root", "key", "start")
            shell._run_remote_monitoring_control("vpn.example", "root", "key", "stop")

        self.assertIn("systemctl is-active --quiet amnezia-dashboard.service", calls[0])
        self.assertIn("systemctl start --no-block amnezia-dashboard.service", calls[0])
        self.assertIn("systemctl start amnezia-traffic-collector.timer", calls[0])
        self.assertIn("collector_state", calls[0])
        self.assertIn("systemctl start --no-block amnezia-traffic-collector.service", calls[0])
        self.assertIn("systemctl stop amnezia-traffic-collector.timer", calls[1])
        self.assertIn("systemctl stop amnezia-dashboard.service", calls[1])

    def test_ensure_tunnel_starts_remote_monitoring_before_using_existing_port(self) -> None:
        app = shell.ShellApp.__new__(shell.ShellApp)
        app._closed = False
        app.manage_remote_monitoring = True
        app._remote_monitoring_started = False
        app.host = "vpn.example"
        app.ssh_user = "root"
        app.key_path = "key"
        app.local_port = 18765

        calls = []

        with patch.object(shell, "_run_remote_monitoring_control", side_effect=lambda *args: calls.append(args)):
            with patch.object(shell, "_test_local_port", return_value=True):
                shell.ShellApp._ensure_tunnel(app)

        self.assertEqual(calls, [("vpn.example", "root", "key", "start")])
        self.assertTrue(app._remote_monitoring_started)


if __name__ == "__main__":
    unittest.main()
