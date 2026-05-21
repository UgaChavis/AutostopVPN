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
- `apply_telegram_mss_fallback.ps1`
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
- the native shell app starts and stops the server-side collector/dashboard services by default
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

If Telegram voice notes, media, or sticker packs pause inside the VPN, do not assume the tunnel MTU is still the only problem. The current server-side baseline is `awg0 MTU=1280`, and the live config should also contain `MTU = 1280`.

Recommended order:

1. Read the current values from the dashboard or `status` output.
2. Capture a read-only Telegram baseline:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 100 -SampleSeconds 30
```

3. Confirm these baseline signals:
   - `Telegram mobile readiness` shows live `awg0` MTU and config MTU equal to `1280`.
   - `Peer keepalive summary` shows whether peers are running with keepalive off and whether handshakes go stale.
   - `Telegram MSS counters` shows whether current Telegram MSS clamp rules are present and receiving traffic.
   - `Gateway jitter` shows provider gateway packet loss and RTT spread.

4. Run a pilot on 2-3 problem phones before changing everyone. For each pilot mobile profile, keep existing keys, endpoint, DNS, and `AllowedIPs`, and set:

```ini
MTU = 1280
PersistentKeepalive = 25
```

5. Test Telegram on each pilot phone after 15 minutes of idle time, media download, voice note playback, media upload, and Wi-Fi/LTE switching.
6. Watch the pilot for 48 hours. The pass condition is no case where Telegram only recovers after restarting the mobile app, no noticeable battery or heat complaints, and no normal browsing regression through the VPN.

If `Transport:` or `Telegram mobile readiness` shows `awg0 MTU` above the recommended value, fix MTU before the keepalive pilot. Apply the runtime test on the server:

```bash
docker exec amnezia-awg2 ip link set dev awg0 mtu 1280
docker exec amnezia-awg2 cat /sys/class/net/awg0/mtu
```

If `1280` fixes the pauses and media loading, make the change persistent in the live container start/config path for `amnezia-awg2`. Put the chosen permanent value into `amnezia_server_info.json` as `wireguard_mtu` so the dashboard shows the same target on future runs.

The local helper `apply_telegram_mtu_fix.ps1` is intentionally treated as high risk because it changes the live container MTU. Preview the planned SSH and SCP operations before applying:

```powershell
.\apply_telegram_mtu_fix.ps1 -DryRun
.\apply_telegram_mtu_fix.ps1 -WhatIf
```

If the mobile pilot does not improve Telegram, the next server-side fallback is a generic TCP MSS clamp through `awg0`. The helper is intentionally not automatic because it changes live container firewall rules and the container start script. Preview it first and run it only during a low-traffic maintenance window:

```powershell
.\apply_telegram_mss_fallback.ps1 -DryRun -NoRestart
.\apply_telegram_mss_fallback.ps1 -WhatIf -NoRestart
```

Do not mass-replace user profiles or make the MSS fallback persistent until the pilot confirms that the safer `MTU=1280` and `PersistentKeepalive=25` profile standard is insufficient.

## Provider Outage Watch Mode

When packet loss is visible on the provider network, keep the VPN runtime untouched and collect evidence instead.

Safe read-only checks from the local workspace:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 10 -SampleSeconds 15
```

Optional light throughput check:

```powershell
.\check_autostopvpn_network.ps1 -DownloadBytes 5000000
```

Allowed during provider instability:

- check GitHub/local parity
- read `systemctl is-active` for the dashboard and collector
- read `docker ps`, `ss -lunp`, `wg show`, and `ip route`
- sample pings to the provider gateway, `1.1.1.1`, and `8.8.8.8`
- sample peer handshake age and traffic counter deltas
- collect provider-facing evidence with timestamps

Avoid while active handshakes still exist:

- rebuilding, replacing, or restarting `amnezia-awg2`
- editing `/opt/amnezia/awg/awg0.conf`
- running `wg set` or removing peers
- changing MTU repeatedly while packet loss is already visible at the provider gateway
- restarting the collector/dashboard loop as a way to diagnose VPN transport

The collector and dashboard can be repaired after provider loss stabilizes. Treat stable gateway ping over several samples as the signal to resume server-side monitoring deployment work.

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
7. Reload `systemd`, run one manual collector execution, start `amnezia-dashboard.service`, and verify `127.0.0.1:18080`.
8. Check the `Transport:` line in the summary output. If the live `awg0` MTU is above the recommended value, lower it and re-test Telegram voice playback.
9. Disable boot-time autostart for `amnezia-dashboard.service` and `amnezia-traffic-collector.timer`; the desktop shell starts them on open and stops them on close:

```bash
systemctl disable --now amnezia-dashboard.service amnezia-traffic-collector.timer
systemctl stop amnezia-traffic-collector.service
systemctl reset-failed amnezia-traffic-collector.service amnezia-dashboard.service
```

10. Open the shell app from Windows; the SSH tunnel is handled inside the app.

For this deployment, the chosen value is `1280`.
The MTU probe result is cached for an hour by default so the collector does not re-run `ping -M do` on every cycle.
Geo labels for peer endpoints are cached too, so normal refresh cycles stay light.
Because the timer can run every second while the shell is open, `amnezia-dashboard.service` should be restarted while the collector timer is stopped during manual maintenance. Otherwise systemd can leave the dashboard start job waiting behind a continuously triggered collector service.

## Rollback

1. Stop `amnezia-dashboard.service`.
2. Stop `amnezia-traffic-collector.timer` while replacing files.
3. Restore the previous `/usr/local/bin/amnezia_traffic_collector.py`.
4. Restore the previous systemd unit files.
5. Restore `/var/lib/amnezia-traffic` from backup if needed.
6. Run `systemctl daemon-reload`.
7. Start `amnezia-dashboard.service` only for verification, then stop it again unless the desktop shell is open.

Rollback does not touch the VPN container itself.

## Windows Launcher

Use `open_amnezia_dashboard.cmd`.

It:

- launches the native Autostop VPN shell window
- starts server-side monitoring services
- starts and hides the SSH tunnel inside the app
- refreshes the view every few seconds while the window is open
- stops server-side monitoring services when the window closes

No public dashboard port is exposed to the internet.
The VPN container `amnezia-awg2` is not restarted by the launcher.
