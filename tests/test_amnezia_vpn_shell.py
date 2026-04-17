from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import amnezia_vpn_shell as shell


class AmneziaVpnShellTests(unittest.TestCase):
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
                    "is_active": True,
                    "handshake_age_seconds": 20,
                    "current_total_bps": 2048,
                    "current_share_percent": 66.67,
                    "today_bytes": 2048,
                    "total_bytes": 4096,
                },
            ],
        }

        model = shell.build_view_model(summary, "http://127.0.0.1:18765/dashboard.json", 5)

        self.assertEqual(model["bandwidth_state_label"], "норма")
        self.assertEqual(model["container_label"], "amnezia-awg2 [работает]")
        self.assertEqual(model["current_total"], "3.00 KiB/s")
        self.assertEqual(model["server_load"], "0.20 / 0.30 / 0.40")
        self.assertEqual(model["server_memory"], "25.00% (1.00 KiB/4.00 KiB)")
        self.assertEqual(model["server_disk"], "25.00% (2.00 KiB/8.00 KiB)")
        self.assertEqual(model["server_ping"], "9.50 ms")
        self.assertEqual(model["peer_rows"][0]["name"], "Peer A")
        self.assertEqual(model["peer_rows"][0]["current"], "2.00 KiB/s")
        self.assertEqual(model["peer_rows"][1]["name"], "Peer B")
        self.assertEqual(model["warnings"], ["first warning"])
        self.assertEqual(model["source_url"], "http://127.0.0.1:18765/dashboard.json")
        self.assertEqual(model["refresh_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
