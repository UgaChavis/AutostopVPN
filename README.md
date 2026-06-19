# Autostop VPN

Autostop VPN is the isolated local working copy for the VPN monitoring subsystem.

The production copy of the same files is mirrored into the main `AutostopCRM` repository. This repo is the place to make VPN-only changes without mixing them with CRM work.

For a fast orientation map, read [CODEX_PROJECT_MAP.md](CODEX_PROJECT_MAP.md). For access notes and external documentation, read [ACCESS_AND_DOCS.md](ACCESS_AND_DOCS.md).

## Documentation Map

- [README.md](README.md): project entrypoint and common commands
- [CODEX_PROJECT_MAP.md](CODEX_PROJECT_MAP.md): compact maintainer map, active files, runtime flow, and verification targets
- [AMNEZIA_VPN_MONITORING.md](AMNEZIA_VPN_MONITORING.md): production runbook, live VPN health checks, Telegram tuning, deployment, and rollback
- [LOCAL_INSTALL.md](LOCAL_INSTALL.md): Windows desktop installation and launcher notes
- [MAINTENANCE.md](MAINTENANCE.md): cleanup rules, documentation classification, and regression checklist
- [ACCESS_AND_DOCS.md](ACCESS_AND_DOCS.md): where to find external access notes without committing secrets
- [AMNEZIA_FULL_ACCESS_RECOVERY.md](AMNEZIA_FULL_ACCESS_RECOVERY.md): restore AmneziaVPN owner/full-access management after local app reinstall

## Current Production Baseline

Last verified from the server on 2026-06-19:

- VPN container `amnezia-awg2` is running and listens on UDP `47895`
- alternate mobile endpoint UDP `443` is forwarded on the host to the existing `47895/udp` listener without restarting the VPN container
- monitoring services are app-managed and normally inactive until the desktop shell opens
- peer config has `68` peers, all with server-side `PersistentKeepalive=25`
- live `awg0` MTU and config MTU are `1280`
- generic `awg0` TCP MSS is clamped to `1240`
- Telegram IPv4 to IPv6 relay is active on the current VPS for provider-blocked Telegram IPv4 endpoints
- Telegram API and Web HTTPS work through the VPN; the read-only monitor retries Telegram HTTPS because a first attempt can fail transiently
- OpenAI HTTPS works through the VPN: unauthenticated `api.openai.com/v1/models` returns the expected HTTP `401`, and `chatgpt.com` reaches Cloudflare
- current channel utilization is well below the 1 Gbps configured limit; the latest collector status showed about `1.12 MiB/s`, `0.94%` utilization, and `0` collector warnings
- recurring provider-gateway jitter spikes can appear without packet loss; the latest read-only samples had `0%` packet loss, with one short gateway sample reaching about `101 ms`
- local Windows client MTU is currently correct: active IPv4/IPv6 `AmneziaVPN` interfaces and the persistent tunnel service show `MTU=1280`
- latest local recovery check finished with `Health failures: 0`; it reported one non-critical warning because local VPN download was `19.13 Mbps`, slightly below the `20 Mbps` warning threshold and above the `5 Mbps` failure threshold; a server-side Cloudflare sample reached about `42.7 Mbps`, so the warning is on the local/client route, not the VPS channel
- no Windows Task Scheduler jobs matching `Autostop` or `VPN` were registered on this workstation during the 2026-06-19 check; run `scheduled_recovery_checks.ps1` manually until recurring jobs are recreated

## What This Project Does

- collects WireGuard peer traffic from the live Amnezia container
- calculates current, daily, and lifetime traffic per peer
- tracks active peers from handshake age
- shows peer rows with approximate city/country based on each peer's public endpoint
- captures server health signals: load, memory, disk, uptime, ping
- estimates current channel utilization, daily average load, day peak, and per-peer share of the live flow
- shows a top-of-page traffic banner with channel load, limit, headroom, and risk state
- renders JSON and text reports and keeps a lightweight HTML fallback view for diagnostics
- serves the latest snapshot on the server side and exposes it to the desktop shell as JSON
- opens a native Windows shell window with the SSH tunnel hidden inside the app
- supports a local installation into `%LOCALAPPDATA%\AutostopVPN` with a desktop shortcut and custom icon

## Repository Layout

- [amnezia_traffic_collector.py](amnezia_traffic_collector.py): telemetry collector, report writer, and dashboard generator
- [amnezia_vpn_shell.py](amnezia_vpn_shell.py): native Windows shell UI for the live VPN snapshot with the operations cockpit theme
- [amnezia_dashboard_server.py](amnezia_dashboard_server.py): request-time dashboard server for fresh page loads
- [amnezia_server_info.json](amnezia_server_info.json): server metadata and editable dashboard notes
- [amnezia-traffic-collector.service](amnezia-traffic-collector.service): systemd unit for scheduled collection
- [amnezia-traffic-collector.timer](amnezia-traffic-collector.timer): systemd timer for the collector
- [amnezia-dashboard.service](amnezia-dashboard.service): localhost HTTP service for the latest dashboard snapshot
- [open_amnezia_dashboard.ps1](open_amnezia_dashboard.ps1): PowerShell launcher for the native shell app
- [open_amnezia_dashboard.cmd](open_amnezia_dashboard.cmd): cmd wrapper for the PowerShell launcher
- [check_autostopvpn_network.ps1](check_autostopvpn_network.ps1): read-only outage monitor for server reachability, peer handshakes, and light traffic deltas
- [scheduled_recovery_checks.ps1](scheduled_recovery_checks.ps1): logged local + server recovery checks for MTU, packet loss, Telegram/OpenAI HTTPS, local download speed, and the read-only monitor
- [repair_local_amnezia_mtu.ps1](repair_local_amnezia_mtu.ps1): elevated local helper for backing up, applying, and rolling back the Windows Amnezia tunnel MTU
- [audit_autostopvpn.ps1](audit_autostopvpn.ps1): read-only maintenance audit for cleanup, docs, risk markers, and tests
- [start_autostopvpn.ps1](start_autostopvpn.ps1): stable desktop entrypoint for the installed app
- [install_autostopvpn.ps1](install_autostopvpn.ps1): copy the project to `%LOCALAPPDATA%\AutostopVPN` and create a desktop shortcut with a generated shield icon
- [remove_autostopvpn.ps1](remove_autostopvpn.ps1): remove the local install and desktop shortcut
- [apply_udp443_forward.ps1](apply_udp443_forward.ps1): high-risk no-drop helper that adds or rolls back the host UDP `443` DNAT forward to the existing VPN listener
- [apply_telegram_keepalive_fix.ps1](apply_telegram_keepalive_fix.ps1): high-risk keepalive helper for all mobile peers with `-DryRun`, `-WhatIf`, and rollback support
- [apply_telegram_mtu_fix.ps1](apply_telegram_mtu_fix.ps1): high-risk MTU helper with `-DryRun` and `-WhatIf` support
- [apply_telegram_mss_fallback.ps1](apply_telegram_mss_fallback.ps1): high-risk Telegram MSS fallback helper with `-DryRun`, `-WhatIf`, `-NoRestart`, and rollback support
- [apply_telegram_ipv6_relay_fix.ps1](apply_telegram_ipv6_relay_fix.ps1): high-risk current-VPS Telegram IPv4 to IPv6 relay helper with `-DryRun`, `-WhatIf`, and rollback support
- [LOCAL_INSTALL.md](LOCAL_INSTALL.md): local install and shortcut instructions
- [AMNEZIA_VPN_MONITORING.md](AMNEZIA_VPN_MONITORING.md): deployment and rollback runbook
- [MAINTENANCE.md](MAINTENANCE.md): cleanup, optimization, and staged-sync checklist
- [tests/test_amnezia_traffic_collector.py](tests/test_amnezia_traffic_collector.py): unit tests for the collector logic

## Runtime Flow

```text
Windows launcher starts the native shell UI
    ->
shell starts amnezia-dashboard.service and amnezia-traffic-collector.timer over SSH
    ->
collector reads docker exec amnezia-awg2 wg show awg0 dump
    ->
collector writes data/ JSON + reports/ CSV/MD + web/ dashboard files
    ->
amnezia-dashboard.service serves dashboard JSON on 127.0.0.1:18080
    ->
shell reads dashboard JSON through its hidden SSH tunnel
    ->
shell stops monitoring services when the window closes
```

## Configuration

The collector is configured through environment variables and `amnezia_server_info.json`.

Environment variables with defaults:

- `AMNEZIA_CONTAINER=amnezia-awg2`
- `AMNEZIA_INTERFACE=awg0`
- `AMNEZIA_TRAFFIC_DIR=/var/lib/amnezia-traffic`
- `AMNEZIA_TRAFFIC_TZ=Asia/Krasnoyarsk`
- `AMNEZIA_PING_TARGET=1.1.1.1`
- `AMNEZIA_ACTIVE_WINDOW_SECONDS=180`
- `AMNEZIA_PING_COUNT=3`
- `AMNEZIA_MTU_PROBE=1`
- `AMNEZIA_MTU_TARGET=1.1.1.1`
- `AMNEZIA_MTU_PROBE_CACHE_SECONDS=3600`

Important `amnezia_server_info.json` fields:

- `hostname`: server hostname
- `public_ip`: external server IP
- `domain`: public CRM or service domain associated with the box
- `ssh_user`: SSH account used by the launcher
- `ssh_port`: SSH port
- `project_path`: deployment path on the server
- `vpn_container`: container name
- `network_interface`: optional interface for bandwidth auto-detection
- `bandwidth_limit_mbps`: optional provider or plan limit in Mbps
- `wireguard_mtu`: optional target MTU for the VPN tunnel if you want the dashboard to compare it against the live probe
- `vpn_public_udp_port`: current stable external VPN port
- `vpn_alternate_udp_ports`: alternate external UDP ports for controlled client rollout
- `notes`: dashboard notes shown to the operator

If `bandwidth_limit_mbps` is empty, the collector falls back to the speed of the detected default network interface.
If `wireguard_mtu` is set, the collector can compare it to the live `awg0` MTU and the probed path MTU.
The current recommended value is `1280` after mobile Telegram media testing.
The current mobile Telegram profile standard is `Endpoint=46.8.254.243:443`, `MTU=1280`, and `PersistentKeepalive=25`.
Existing profiles on `46.8.254.243:47895` continue to work; update phones to UDP `443` during the client rollout.
Server-side peer keepalive is managed by `apply_telegram_keepalive_fix.ps1`; phone profiles should still be updated or re-imported with the same values for the best mobile NAT behavior.
The post-keepalive server fallback is a generic TCP MSS clamp through `awg0` at `1240`. The read-only monitor reports Telegram-specific rule counters separately; on the current baseline, the expected active fallback signal is `generic_awg0_mss_in=1` and `generic_awg0_mss_out=1`.
The collector also caches endpoint geo labels and MTU probe results so normal refresh cycles stay light.

## Client Rollout Impact

The server-side fixes are already active for every peer that connects through this VPN: `awg0 MTU=1280`, generic TCP MSS clamp `1240`, server peer `PersistentKeepalive=25`, Cloudflare resolver on the VPS/container, and the UDP `443` DNAT endpoint. These do not require users to reconnect or re-import a profile before they benefit from the server path.

Client profile fields are different: existing devices keep whatever is stored locally in the Amnezia/WireGuard app. The server cannot remotely rewrite a phone or desktop profile from `47895` to `443`, add client-side `MTU=1280`, or add client-side `PersistentKeepalive=25`.

Roll out new profiles in this order:

1. Update phones and users who reported Telegram/media slowdowns first.
2. Use `Endpoint = 46.8.254.243:443`, `MTU = 1280`, and `PersistentKeepalive = 25`.
3. Keep existing keys, `AllowedIPs`, and user identity; only transport/MTU/keepalive need to change.
4. Leave healthy desktop users on `47895` until their normal maintenance window; `47895/udp` remains active.
5. Treat stale or never-handshaked peers as inactive inventory. Reissue them with the current profile standard when the user returns.

After a profile update, ask the user to disconnect/reconnect the VPN, leave the device idle for 10-15 minutes, then test normal browsing plus Telegram text, media download, media upload, and voice notes.

## Generated Data

The collector writes into `amnezia_traffic_collector.py`'s data directory:

- `state.json`: last sample state
- `totals.json`: accumulated totals per peer
- `summary.json`: current dashboard snapshot
- `summary.json` includes `server.bandwidth` with channel load and headroom
- the dashboard server serves the latest `summary.json` on each request; the shell app reads it over the SSH tunnel
- `daily/YYYY-MM-DD.json`: day-level counters
- `reports/current_users.csv`: peer report
- `reports/current_users.md`: peer report in markdown
- `web/dashboard.json`: JSON version of the dashboard
- `web/index.html`: HTML fallback dashboard for technical diagnostics
- `aliases.csv`: optional peer labels

## Local Work

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Run the maintenance audit before cleanup or optimization work:

```powershell
.\audit_autostopvpn.ps1 -RunTests
```

Generate reports without talking to Docker:

```powershell
python .\amnezia_traffic_collector.py report
```

Run a live collection pass on a machine that has Docker and `ping`:

```powershell
python .\amnezia_traffic_collector.py collect
```

Open the shell UI locally through SSH:

```powershell
.\open_amnezia_dashboard.ps1
```

The launcher checks `AUTOSTOPVPN_SSH_KEY` and `AUTOSTOPCRM_SSH_KEY` first, then falls back to `autostopvpn_server_ed25519`, `autostopcrm_server_ed25519`, `codex_autostopvpn`, `codex_autostopcrm`, and `codex_autostopcrm_key` in `~/.ssh`. It then opens the native shell window without a second console or browser window.
By default, the shell starts `amnezia-dashboard.service` and `amnezia-traffic-collector.timer` on the server when the window opens, refreshes the live snapshot at a 1s interval, and stops those monitoring services when the window closes. The VPN container is not restarted or reconfigured by this flow. Use `--no-manage-remote-monitoring` only for manual server-side maintenance.

Inspect the latest cached state:

```powershell
python .\amnezia_traffic_collector.py status
```

Check local prerequisites:

```powershell
python .\amnezia_traffic_collector.py doctor
```

During a provider outage, use the read-only monitor from the local workspace:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 10 -SampleSeconds 15
```

For a Telegram mobile baseline before changing phone profiles, use a longer read-only sample:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 100 -SampleSeconds 30
```

The monitor includes Telegram-focused read-only sections:

- `Telegram mobile readiness`: live `awg0` MTU, config MTU, and the mobile client standard
- `Alternate UDP endpoint`: UDP `443` forward presence, service state, DNAT counters, and confirmation that `47895/udp` remains published
- `Peer keepalive summary`: keepalive counts and stale handshakes
- `Telegram MSS counters`: current Telegram-specific counters and generic `awg0` MSS fallback counters inside the container
- `Telegram API availability`: DNS resolution, HTTPS retry timing, HTTP status, and Telegram ICMP loss
- `OpenAI API availability`: DNS and HTTPS/SNI timing for `api.openai.com` and `chatgpt.com`
- `Ping 1.1.1.1` and `Ping 1.0.0.1`: critical Cloudflare egress/DNS path checks
- `Ping 8.8.8.8`: non-critical Google route comparison; intermittent loss here is logged as a warning when Cloudflare and Telegram are healthy
- `Telegram IPv4 to IPv6 relay state`: current-VPS relay service, rules, counters, and warning log state
- `Gateway jitter`: parsed packet loss and RTT spread to the provider gateway

For a small server-side download sample, opt in explicitly:

```powershell
.\check_autostopvpn_network.ps1 -DownloadBytes 5000000
```

The monitor does not restart services, change peer configuration, or change MTU. It only reads service state, `wg show` counters, routes, pings, and the optional HTTP sample.

Run a logged recovery check from the Windows desktop after a VPN stability incident:

```powershell
.\scheduled_recovery_checks.ps1 -PingCount 30 -SampleSeconds 30 -LocalDownloadBytes 10485760 -WarnLocalDownloadMbps 20 -MinLocalDownloadMbps 5
```

Logs are written under `%LOCALAPPDATA%\AutostopVPN\logs`. The local download probe uses a 10 MB Cloudflare sample with a browser user-agent, warns below 20 Mbps, and fails below 5 Mbps.

Current recovery baseline: the server and `amnezia-awg2` resolver are set to Cloudflare DNS (`1.1.1.1`, `1.0.0.1`). Google DNS had much higher RTT from the VPS and occasional ICMP loss, so it is no longer treated as a critical health dependency.
As of 2026-06-19, no local Windows Task Scheduler jobs matching `Autostop` or `VPN` were registered on this workstation. Run the command above manually after incidents until the recurring checks are recreated.
The intended short recurring profile is `-PingCount 10`, `-SampleSeconds 5`, and `-LocalDownloadBytes 5242880` so every 15-minute health check finishes quickly.
The intended daily deep-check profile runs at 08:00 Asia/Krasnoyarsk with `-PingCount 60`, `-SampleSeconds 30`, `-DownloadBytes 52428800`, and `-LocalDownloadBytes 52428800`, writing logs under `%LOCALAPPDATA%\AutostopVPN\logs`.

If the scheduled check reports active local `AmneziaVPN` MTU `1376`, repair only the local Windows client from an elevated PowerShell session. This backs up the secret-bearing tunnel service registry key under `%LOCALAPPDATA%\AutostopVPN\secret-backups` before changing the active IPv4/IPv6 interface MTU and persistent service ImagePath:

```powershell
.\repair_local_amnezia_mtu.ps1 -DryRun
.\repair_local_amnezia_mtu.ps1 -WhatIf
.\repair_local_amnezia_mtu.ps1
.\repair_local_amnezia_mtu.ps1 -Rollback -BackupPath "%LOCALAPPDATA%\AutostopVPN\secret-backups\AmneziaWGTunnel-AmneziaVPN-YYYYMMDD-HHMMSS.reg"
```

The stable mobile profile target for phones is:

```ini
Endpoint = 46.8.254.243:443
MTU = 1280
PersistentKeepalive = 25
```

The UDP `443` helper changes only host NAT and a dedicated systemd oneshot service. It does not restart or recreate `amnezia-awg2`; `47895/udp` stays active for existing devices:

```powershell
.\apply_udp443_forward.ps1 -DryRun
.\apply_udp443_forward.ps1 -WhatIf
.\apply_udp443_forward.ps1
.\apply_udp443_forward.ps1 -Rollback
```

The Telegram keepalive helper changes live WireGuard peer keepalive and edits the live container config. Preview it before applying, and keep the printed backup path for rollback:

```powershell
.\apply_telegram_keepalive_fix.ps1 -DryRun
.\apply_telegram_keepalive_fix.ps1 -WhatIf
.\apply_telegram_keepalive_fix.ps1
.\apply_telegram_keepalive_fix.ps1 -RollbackBackupPath /root/autostopvpn-backups/awg0.conf.keepalive.bak.YYYYMMDD-HHMMSS
```

The Telegram MTU helper changes the live container. Preview it before applying:

```powershell
.\apply_telegram_mtu_fix.ps1 -DryRun
.\apply_telegram_mtu_fix.ps1 -WhatIf
```

The Telegram MSS fallback helper changes live container firewall rules and the container start script. It is the current post-keepalive fallback path:

```powershell
.\apply_telegram_mss_fallback.ps1 -DryRun -NoRestart
.\apply_telegram_mss_fallback.ps1 -WhatIf -NoRestart
.\apply_telegram_mss_fallback.ps1 -NoRestart
.\apply_telegram_mss_fallback.ps1 -RollbackBackupPath /root/autostopvpn-backups/start.sh.mss.bak.YYYYMMDD-HHMMSS -NoRestart
```

The Telegram IPv4 to IPv6 relay helper is the current provider-route repair for Telegram on this VPS. It does not change Amnezia peer profiles, MTU, MSS, keepalive, or the VPN listener. It installs a host systemd service and narrow iptables rules that redirect only known broken Telegram IPv4 TCP `80/443` destinations from the VPN container and local VPS traffic to reachable Telegram IPv6 DC endpoints:

```powershell
.\apply_telegram_ipv6_relay_fix.ps1 -DryRun
.\apply_telegram_ipv6_relay_fix.ps1 -WhatIf
.\apply_telegram_ipv6_relay_fix.ps1
.\apply_telegram_ipv6_relay_fix.ps1 -Rollback
```

Rollback removes `autostopvpn-telegram-relay.service`, `/usr/local/sbin/autostopvpn-telegram-relay.py`, `/usr/local/sbin/autostopvpn-telegram-relay-rules.sh`, and the dedicated relay iptables rules only.

## Working Model

Use this repository for local VPN-only edits:

1. change the collector, launcher, services, or docs in this repo
2. run the unit tests here
3. copy the same files into the mirrored location in `AutostopCRM`
4. keep the operational docs in sync between both copies

This keeps VPN work isolated locally while still shipping the final files through the main CRM repository.

## Local Install

For the Windows desktop workflow, install the project into the user system folder and create a desktop shortcut:

```powershell
.\install_autostopvpn.ps1
```

That copies the current repo to `%LOCALAPPDATA%\AutostopVPN`, generates `AutostopVPN.ico`, and creates `Autostop VPN.lnk` on the desktop.

Use the shortcut or run:

```powershell
.\start_autostopvpn.ps1
```

## Server Deployment

This repo is the local working copy. The production deployment still uses the same files on the server-side CRM environment.

Typical deployment targets:

- collector binary: `/usr/local/bin/amnezia_traffic_collector.py`
- runtime data: `/var/lib/amnezia-traffic`
- collector service: `amnezia-traffic-collector.service`
- collector timer: `amnezia-traffic-collector.timer`
- dashboard service: `amnezia-dashboard.service`
- shell app: `amnezia_vpn_shell.py`

See [AMNEZIA_VPN_MONITORING.md](AMNEZIA_VPN_MONITORING.md) for the step-by-step deployment and rollback flow.

## Notes

- The collector never changes WireGuard peer configuration.
- The dashboard API is intentionally localhost-only on the server.
- The collector and dashboard are app-managed by default: they should be disabled for boot-time autostart and started by the desktop shell only while the shell is open.
- If you change the server host, SSH key, or project path, update `amnezia_server_info.json` first.
