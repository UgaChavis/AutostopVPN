# Autostop VPN

Autostop VPN is the isolated local working copy for the VPN monitoring subsystem.

The production copy of the same files is mirrored into the main `AutostopCRM` repository. This repo is the place to make VPN-only changes without mixing them with CRM work.

For a fast orientation map, read [CODEX_PROJECT_MAP.md](CODEX_PROJECT_MAP.md). For access notes and external documentation, read [ACCESS_AND_DOCS.md](ACCESS_AND_DOCS.md).

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
- [amnezia_vpn_shell.py](amnezia_vpn_shell.py): native Windows shell UI for the live VPN snapshot with the cyberpunk dashboard theme
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
- [apply_telegram_mtu_fix.ps1](apply_telegram_mtu_fix.ps1): high-risk MTU helper with `-DryRun` and `-WhatIf` support
- [apply_telegram_mss_fallback.ps1](apply_telegram_mss_fallback.ps1): high-risk Telegram MSS fallback helper with `-DryRun`, `-WhatIf`, and `-NoRestart` support
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
- `notes`: dashboard notes shown to the operator

If `bandwidth_limit_mbps` is empty, the collector falls back to the speed of the detected default network interface.
If `wireguard_mtu` is set, the collector can compare it to the live `awg0` MTU and the probed path MTU.
The current recommended value is `1280` after mobile Telegram media testing.
The current mobile Telegram pilot profile standard is `MTU=1280` and `PersistentKeepalive=25`.
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

For a Telegram mobile baseline before changing pilot phones, use a longer read-only sample:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 100 -SampleSeconds 30
```

The monitor includes Telegram-focused read-only sections:

- `Telegram mobile readiness`: live `awg0` MTU, config MTU, and the pilot client standard
- `Peer keepalive summary`: keepalive counts and stale handshakes
- `Telegram MSS counters`: current Telegram MSS clamp counters inside the container
- `Gateway jitter`: parsed packet loss and RTT spread to the provider gateway

For a small server-side download sample, opt in explicitly:

```powershell
.\check_autostopvpn_network.ps1 -DownloadBytes 5000000
```

The monitor does not restart services, change peer configuration, or change MTU. It only reads service state, `wg show` counters, routes, pings, and the optional HTTP sample.

The Telegram MTU helper changes the live container. Preview it before applying:

```powershell
.\apply_telegram_mtu_fix.ps1 -DryRun
.\apply_telegram_mtu_fix.ps1 -WhatIf
```

The Telegram MSS fallback helper changes live container firewall rules and the container start script. It is prepared for the post-pilot fallback path only:

```powershell
.\apply_telegram_mss_fallback.ps1 -DryRun -NoRestart
.\apply_telegram_mss_fallback.ps1 -WhatIf -NoRestart
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
