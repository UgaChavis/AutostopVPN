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
- [amnezia_vpn_shell.py](amnezia_vpn_shell.py): native Windows shell UI for the live VPN snapshot
- [amnezia_dashboard_server.py](amnezia_dashboard_server.py): request-time dashboard server for fresh page loads
- [amnezia_server_info.json](amnezia_server_info.json): server metadata and editable dashboard notes
- [amnezia-traffic-collector.service](amnezia-traffic-collector.service): systemd unit for scheduled collection
- [amnezia-traffic-collector.timer](amnezia-traffic-collector.timer): systemd timer for the collector
- [amnezia-dashboard.service](amnezia-dashboard.service): localhost HTTP service for the latest dashboard snapshot
- [open_amnezia_dashboard.ps1](open_amnezia_dashboard.ps1): PowerShell launcher for the native shell app
- [open_amnezia_dashboard.cmd](open_amnezia_dashboard.cmd): cmd wrapper for the PowerShell launcher
- [start_autostopvpn.ps1](start_autostopvpn.ps1): stable desktop entrypoint for the installed app
- [install_autostopvpn.ps1](install_autostopvpn.ps1): copy the project to `%LOCALAPPDATA%\AutostopVPN` and create a desktop shortcut with a generated shield icon
- [remove_autostopvpn.ps1](remove_autostopvpn.ps1): remove the local install and desktop shortcut
- [apply_telegram_mtu_fix.ps1](apply_telegram_mtu_fix.ps1): apply the server-side MTU helper and sync the dashboard metadata
- [LOCAL_INSTALL.md](LOCAL_INSTALL.md): local install and shortcut instructions
- [AMNEZIA_VPN_MONITORING.md](AMNEZIA_VPN_MONITORING.md): deployment and rollback runbook
- [tests/test_amnezia_traffic_collector.py](tests/test_amnezia_traffic_collector.py): unit tests for the collector logic

## Runtime Flow

```text
docker exec amnezia-awg2 wg show awg0 dump
    ->
collector builds traffic, activity, and server summary
    ->
data/ JSON + reports/ CSV/MD + web/ dashboard files
    ->
amnezia-dashboard.service serves dashboard JSON on 127.0.0.1:18080
    ->
Windows launcher opens SSH tunnel and starts the shell UI
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
The current recommended value is `1380`.

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
The shell keeps refreshing the live snapshot while it is open, and the collector timer on the server now runs every 10 seconds by default.

Inspect the latest cached state:

```powershell
python .\amnezia_traffic_collector.py status
```

Check local prerequisites:

```powershell
python .\amnezia_traffic_collector.py doctor
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
- If you change the server host, SSH key, or project path, update `amnezia_server_info.json` first.
