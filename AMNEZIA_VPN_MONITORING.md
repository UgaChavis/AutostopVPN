# Amnezia VPN Monitoring

This document is the operator runbook for the VPN monitoring subsystem.

The files in this repository are the local working copy. The production mirror lives in the main `AutostopCRM` repository.

## Scope

This adds a low-risk monitoring layer around the existing Amnezia/WireGuard VPN without rebuilding the VPN container or changing its peer configuration.

## Current Server Layout

- VPN runtime: Docker container `amnezia-awg2`
- VPN implementation: AmneziaWG on interface `awg0`
- Public listener: UDP `47895`
- Host source path for the image build context: `/opt/amnezia/amnezia-awg2`
- Live config inside the container: `/opt/amnezia/awg/awg0.conf`
- Existing telemetry data dir: `/var/lib/amnezia-traffic`
- Production application repo on server: `/opt/autostopcrm`

## Why The VPN Container Is Not Updated

The live VPN config is stored inside the container filesystem, not on a bind-mounted host path. Because of that, rebuilding or replacing `amnezia-awg2` is not currently safe: it risks losing or desynchronizing peer configuration. The safe change boundary is the external monitoring layer only.

## Files In This Repo

- `amnezia_traffic_collector.py`
- `amnezia-traffic-collector.service`
- `amnezia-traffic-collector.timer`
- `amnezia-dashboard.service`
- `amnezia_server_info.json`
- `open_amnezia_dashboard.ps1`
- `open_amnezia_dashboard.cmd`
- `tests/test_amnezia_traffic_collector.py`

## What The Collector Produces

- `totals.json`: accumulated per-peer traffic
- `daily/YYYY-MM-DD.json`: daily traffic deltas
- `summary.json`: current server, ping and VPN summary
- `summary.json` includes current channel load, utilization and headroom
- `server_info.json`: editable provider/server/payment card for the dashboard
- `reports/current_users.csv`: machine-readable peer report
- `reports/current_users.md`: text report
- `web/dashboard.json`: dashboard JSON
- `web/index.html`: lightweight local dashboard

## Metrics

- traffic per peer
- sortable peer table by name, IP, handshake, current rate, daily rate and total traffic
- current channel load versus configured or auto-detected bandwidth limit
- per-peer share of the live flow for quick hotspot detection
- explicit traffic periods for current day and full accounting interval
- current speeds based on the latest sample window
- average speed for the current day
- active peer count based on recent handshake age
- server load, memory, disk and uptime
- ping latency and packet loss
- warning list for degraded conditions

## Local Layout

This repository is intentionally flat:

- collector and helper scripts live at the root
- unit tests live in `tests/`
- runtime data is created under the path from `AMNEZIA_TRAFFIC_DIR`

## Environment

The collector reads these environment variables:

- `AMNEZIA_CONTAINER`
- `AMNEZIA_INTERFACE`
- `AMNEZIA_TRAFFIC_DIR`
- `AMNEZIA_TRAFFIC_TZ`
- `AMNEZIA_PING_TARGET`
- `AMNEZIA_ACTIVE_WINDOW_SECONDS`
- `AMNEZIA_PING_COUNT`

## Safe Deployment Outline

1. Create a timestamped backup directory on the server.
2. Copy the current collector binary, systemd units and `/var/lib/amnezia-traffic` into that backup.
3. Copy the updated collector and unit files from this repo to the server.
4. Copy `amnezia_server_info.json` to `/var/lib/amnezia-traffic/server_info.json` and adjust provider or billing notes if needed.
5. If the provider cap is known, set `bandwidth_limit_mbps`; otherwise the collector will fall back to the default network interface speed when available.
6. Run one manual collector execution.
7. Reload `systemd`, restart the collector timer, enable the localhost dashboard service and verify `127.0.0.1:18080`.
8. Open the page through an SSH tunnel from Windows.

## Rollback

1. Stop `amnezia-dashboard.service`.
2. Restore the previous `/usr/local/bin/amnezia_traffic_collector.py`.
3. Restore the previous systemd unit files.
4. Restore `/var/lib/amnezia-traffic` from backup if needed.
5. Run `systemctl daemon-reload`.
6. Restart `amnezia-traffic-collector.timer`.

Rollback does not touch the VPN container itself.

## Windows Launcher

Use `open_amnezia_dashboard.cmd`.

It:

- opens an SSH tunnel from `127.0.0.1:18765` to `127.0.0.1:18080` on the server
- reuses an existing tunnel when possible
- opens the dashboard in the default browser

No public dashboard port is exposed to the internet.
