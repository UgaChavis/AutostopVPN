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

## Current Production Baseline

Last verified from the server on 2026-06-01:

- VPN container `amnezia-awg2` is running and listens on UDP `47895`
- alternate mobile endpoint UDP `443` is forwarded on the host to the existing `47895/udp` listener without restarting the VPN container
- monitoring services are app-managed and normally inactive until the desktop shell opens
- peer config has `57` peers, all with server-side `PersistentKeepalive=25`
- live `awg0` MTU and config MTU are `1280`
- generic `awg0` TCP MSS is clamped to `1240`
- current channel utilization is well below the 1 Gbps configured limit
- recurring provider-gateway jitter spikes can appear without packet loss; collect `check_autostopvpn_network.ps1` output before changing runtime settings

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
- [audit_autostopvpn.ps1](audit_autostopvpn.ps1): read-only maintenance audit for cleanup, docs, risk markers, and tests
- [start_autostopvpn.ps1](start_autostopvpn.ps1): stable desktop entrypoint for the installed app
- [install_autostopvpn.ps1](install_autostopvpn.ps1): copy the project to `%LOCALAPPDATA%\AutostopVPN` and create a desktop shortcut with a generated shield icon
- [remove_autostopvpn.ps1](remove_autostopvpn.ps1): remove the local install and desktop shortcut
- [apply_udp443_forward.ps1](apply_udp443_forward.ps1): high-risk no-drop helper that adds or rolls back the host UDP `443` DNAT forward to the existing VPN listener
- [apply_telegram_keepalive_fix.ps1](apply_telegram_keepalive_fix.ps1): high-risk keepalive helper for all mobile peers with `-DryRun`, `-WhatIf`, and rollback support
- [apply_telegram_mtu_fix.ps1](apply_telegram_mtu_fix.ps1): high-risk MTU helper with `-DryRun` and `-WhatIf` support
- [apply_telegram_mss_fallback.ps1](apply_telegram_mss_fallback.ps1): high-risk Telegram MSS fallback helper with `-DryRun`, `-WhatIf`, `-NoRestart`, and rollback support
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
- `Gateway jitter`: parsed packet loss and RTT spread to the provider gateway

For a small server-side download sample, opt in explicitly:

```powershell
.\check_autostopvpn_network.ps1 -DownloadBytes 5000000
```

The monitor does not restart services, change peer configuration, or change MTU. It only reads service state, `wg show` counters, routes, pings, and the optional HTTP sample.

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
