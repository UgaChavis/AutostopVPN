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
- `amnezia_dashboard_server.py`
- `amnezia_vpn_shell.py`
- `open_amnezia_dashboard.ps1`
- `open_amnezia_dashboard.cmd`
- `apply_telegram_mtu_fix.ps1`
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
- `web/index.html`: lightweight local dashboard fallback
- `/`: dashboard server response rendered from the latest `summary.json` on each request

## Metrics

- traffic per peer
- sortable peer table by name, IP, handshake, current rate, daily rate and total traffic
- current channel load versus configured or auto-detected bandwidth limit
- current load, daily average, and daily peak bandwidth for the server channel
- a top banner that shows whether the channel is normal, near limit, or overloaded
- the native shell app refreshes the latest snapshot automatically while it is open
- per-peer share of the live flow for quick hotspot detection
- explicit traffic periods for current day and full accounting interval
- current speeds based on the latest sample window
- average speed for the current day
- active peer count based on recent handshake age
- server load, memory, disk and uptime
- ping latency and packet loss
- path MTU probing and the live `awg0` MTU from inside the VPN container
- warning list for degraded conditions

## Telegram Stability Fix

If Telegram voice notes, media, or sticker packs pause inside the VPN, the first server-side fix is to lower the tunnel MTU.

Recommended order:

1. Read the current values from the dashboard or `status` output.
2. If `Transport:` shows `awg0 MTU` above the recommended value, try `1380` first.
3. Apply the runtime test on the server:

```bash
docker exec amnezia-awg2 ip link set dev awg0 mtu 1380
docker exec amnezia-awg2 cat /sys/class/net/awg0/mtu
```

4. Re-test Telegram voice playback and media loading.
5. If it is still unstable, try `1360`.
6. If `1380` fixes the pauses, make the change persistent in the live container start/config path for `amnezia-awg2`.
7. Put the chosen permanent value into `amnezia_server_info.json` as `wireguard_mtu` so the dashboard shows the same target on future runs.

This is a tunnel-level fix. Telegram itself does not need any special configuration if the VPN path is clean.

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
- `AMNEZIA_MTU_PROBE`
- `AMNEZIA_MTU_TARGET`
- `AMNEZIA_MTU_PROBE_CACHE_SECONDS`

## Safe Deployment Outline

1. Create a timestamped backup directory on the server.
2. Copy the current collector binary, systemd units and `/var/lib/amnezia-traffic` into that backup.
3. Copy the updated collector and unit files from this repo to the server.
4. Copy `amnezia_server_info.json` to `/var/lib/amnezia-traffic/server_info.json` and adjust provider or billing notes if needed.
5. If the provider cap is known, set `bandwidth_limit_mbps`; otherwise the collector will fall back to the default network interface speed when available.
6. Run one manual collector execution.
7. Reload `systemd`, restart the collector timer, enable the localhost dashboard service and verify `127.0.0.1:18080`.
8. Check the `Transport:` line in the summary output. If the live `awg0` MTU is above the recommended value, lower it and re-test Telegram voice playback.
9. The collector timer refreshes every second to keep the shell snapshot fresh while the app is open.
10. Open the shell app from Windows; the SSH tunnel is handled inside the app.

For this deployment, the chosen value is `1380`.
The MTU probe result is cached for an hour by default so the collector does not re-run `ping -M do` on every cycle.
Geo labels for peer endpoints are cached too, so normal refresh cycles stay light.

## Rollback

1. Stop `amnezia-dashboard.service`.
2. Restore the previous `/usr/local/bin/amnezia_traffic_collector.py`.
3. Restore the previous systemd unit files.
4. Restore `/var/lib/amnezia-traffic` from backup if needed.
5. Run `systemctl daemon-reload`.
6. Restart `amnezia-traffic-collector.timer`. The timer is expected to run every second in the current setup.

Rollback does not touch the VPN container itself.

## Windows Launcher

Use `open_amnezia_dashboard.cmd`.

It:

- launches the native Autostop VPN shell window
- starts and hides the SSH tunnel inside the app
- refreshes the view every few seconds while the window is open

No public dashboard port is exposed to the internet.
