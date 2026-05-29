# Amnezia VPN Monitoring

This document is the operator runbook for the VPN monitoring subsystem.

The files in this repository are the local working copy. The production mirror lives in the main `AutostopCRM` repository.

## Scope

This documents the monitoring layer and the controlled operational helpers around the existing Amnezia/WireGuard VPN. Normal monitoring is read-only; the Telegram keepalive, MTU, and MSS helpers are high-risk maintenance tools and must be run only from the documented workflow.

## Current Server Layout

- VPN runtime: Docker container `amnezia-awg2`
- VPN implementation: AmneziaWG on interface `awg0`
- Public listener: UDP `47895`
- Host source path for the image build context: `/opt/amnezia/amnezia-awg2`
- Live config inside the container: `/opt/amnezia/awg/awg0.conf`
- Existing telemetry data dir: `/var/lib/amnezia-traffic`
- Production application repo on server: `/opt/autostopcrm`

## Current Health Baseline

Last verified from the server on 2026-05-29:

- container `amnezia-awg2` is running and has been up for more than two weeks
- UDP `47895` is listening on IPv4 and IPv6
- `57` peers are configured; server-side `PersistentKeepalive=25` is active for all peers
- live `awg0` MTU and config MTU are both `1280`
- Telegram-specific MSS clamp and generic `awg0` TCP MSS clamp are both active at `1240`
- `api.telegram.org`, `1.1.1.1`, and `8.8.8.8` show `0%` packet loss in the latest checks
- server load, memory, bandwidth utilization, and collector warnings are normal
- provider gateway RTT can spike above `100 ms` without packet loss; treat this as provider jitter evidence, not as a reason to restart the VPN container

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
- `check_autostopvpn_network.ps1`
- `audit_autostopvpn.ps1`
- `apply_telegram_keepalive_fix.ps1`
- `apply_telegram_mtu_fix.ps1`
- `apply_telegram_mss_fallback.ps1`
- `tests/test_amnezia_traffic_collector.py`
- `tests/test_amnezia_vpn_shell.py`
- `tests/test_maintenance_artifacts.py`

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

4. For the current private VPN rollout, apply keepalive to all server peers with the helper below, then update or re-import every mobile profile with the same client-side standard. Keep existing keys, endpoint, DNS, and `AllowedIPs`, and set:

```ini
MTU = 1280
PersistentKeepalive = 25
```

5. Test Telegram on each phone after 15 minutes of idle time, media download, voice note playback, media upload, and Wi-Fi/LTE switching.
6. Watch the rollout for 30-60 minutes first, then a normal workday. The pass condition is no case where Telegram only recovers after restarting the mobile app, no noticeable battery or heat complaints, and no normal browsing regression through the VPN.

If Telegram still improves but remains imperfect after the current `1280/1240` baseline, do not immediately lower only the server MTU. First confirm that the phone profile itself contains `MTU = 1280` and `PersistentKeepalive = 25`, and that the phone OS is not battery-throttling the VPN app or Telegram. The next controlled experiments are:

- client-side `PersistentKeepalive = 15` on affected phones
- an alternate UDP `443` endpoint that forwards to the existing VPN listener
- an MTU ladder only if packet-size symptoms remain: `MTU=1200` with matching `MSS=1160`, then `MTU=1180` with `MSS=1140`
- a Telegram-native MTProxy/SOCKS5 path if the problem is isolated to Telegram while general VPN traffic is healthy

The local helper `apply_telegram_keepalive_fix.ps1` changes live WireGuard peer keepalive and edits the live container config. It backs up `awg0.conf` to `/root/autostopvpn-backups/awg0.conf.keepalive.bak.<timestamp>` before applying changes. Preview and keep the backup path:

```powershell
.\apply_telegram_keepalive_fix.ps1 -DryRun
.\apply_telegram_keepalive_fix.ps1 -WhatIf
.\apply_telegram_keepalive_fix.ps1
.\apply_telegram_keepalive_fix.ps1 -RollbackBackupPath /root/autostopvpn-backups/awg0.conf.keepalive.bak.YYYYMMDD-HHMMSS
```

If `Transport:` or `Telegram mobile readiness` shows `awg0 MTU` above the recommended value, fix MTU before the keepalive rollout. Apply the runtime test on the server:

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

If the keepalive rollout does not improve Telegram, the next server-side fallback is a generic TCP MSS clamp through `awg0`, or a dedicated Telegram MTProxy/SOCKS5 path. The current post-keepalive fallback is the generic TCP MSS clamp at `1240`, alongside the existing Telegram-specific rules. The MSS helper changes live container firewall rules and the container start script, and backs up `/opt/amnezia/start.sh` to `/root/autostopvpn-backups/start.sh.mss.bak.<timestamp>` before applying changes. Preview it first and run it only during a low-traffic maintenance window:

```powershell
.\apply_telegram_mss_fallback.ps1 -DryRun -NoRestart
.\apply_telegram_mss_fallback.ps1 -WhatIf -NoRestart
.\apply_telegram_mss_fallback.ps1 -NoRestart
.\apply_telegram_mss_fallback.ps1 -RollbackBackupPath /root/autostopvpn-backups/start.sh.mss.bak.YYYYMMDD-HHMMSS -NoRestart
```

After applying the generic MSS fallback, watch normal browsing, Telegram media loading, idle reconnect, and voice notes. Roll back from the printed `start.sh.mss.bak.<timestamp>` path if non-Telegram traffic noticeably regresses.

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
- applying keepalive rollout while provider loss is visible
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
