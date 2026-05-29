# AutostopVPN Project Map

This file is the project orientation sheet for Codex and maintainers.

Last verified from the live server on 2026-05-29.

## Canonical Locations

- Local workspace: `%USERPROFILE%\Desktop\AutostopVPN`
- Local install target: `%LOCALAPPDATA%\AutostopVPN`
- Desktop launcher: `%USERPROFILE%\Desktop\Autostop VPN.lnk`
- GitHub VPN branch: `autostopVPN`
- Server mirror path: `/opt/autostopcrm`
- External access documentation root: `%USERPROFILE%\Мой диск\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM`

## Main Files

- `amnezia_traffic_collector.py`: collector, report builder, `collect`, `report`, `status`, `doctor`
- `amnezia_vpn_shell.py`: native desktop shell that displays the live snapshot with the cyberpunk dashboard theme and faster refresh cadence
- `amnezia_dashboard_server.py`: request-time dashboard server for fresh page loads
- collector tracks current channel utilization, daily average load, and daily peak bandwidth
- the dashboard shows a top traffic banner with load, headroom, and risk state
- `amnezia_server_info.json`: server metadata, SSH details, bandwidth limit, notes
- `open_amnezia_dashboard.ps1` and `open_amnezia_dashboard.cmd`: Windows shell launchers
- `check_autostopvpn_network.ps1`: read-only outage monitor for SSH, provider loss, WireGuard handshakes, Telegram mobile readiness, MSS counters, gateway jitter, and traffic deltas
- `apply_telegram_keepalive_fix.ps1`: high-risk helper for applying `PersistentKeepalive=25` to all peers with `-DryRun`, `-WhatIf`, and rollback support
- `apply_telegram_mtu_fix.ps1`: high-risk helper for live `awg0` MTU changes with `-DryRun` and `-WhatIf`
- `apply_telegram_mss_fallback.ps1`: high-risk helper for post-keepalive generic TCP MSS fallback with `-DryRun`, `-WhatIf`, `-NoRestart`, and rollback support
- `audit_autostopvpn.ps1`: read-only maintenance audit for status, size hotspots, stale markers, hard-coded paths, ignored artifacts, and tests
- current Telegram fallback state is server keepalive on all peers, `awg0` MTU `1280`, Telegram-specific MSS `1240`, and generic `awg0` TCP MSS `1240`
- current provider observation: occasional gateway RTT spikes above `100 ms` with `0%` packet loss
- the shell app keeps the SSH tunnel hidden, starts server monitoring while the window is open, stops it on close, and uses a single desktop window
- `start_autostopvpn.ps1`: stable desktop entrypoint
- `install_autostopvpn.ps1`: local install and desktop shortcut creation
- `remove_autostopvpn.ps1`: local uninstall
- `AMNEZIA_VPN_MONITORING.md`: operator runbook
- `LOCAL_INSTALL.md`: local install guide
- `MAINTENANCE.md`: cleanup, optimization, and staged-sync checklist
- `tests/test_amnezia_traffic_collector.py`: logic tests
- `tests/test_amnezia_vpn_shell.py`: shell view-model and refresh tests
- `ACCESS_AND_DOCS.md`: repository navigation and access-key lookup guide

## Runtime Flow

1. Read peer and server state from Docker and host probes.
2. Build current peer rows and traffic deltas.
3. Write JSON, CSV, MD, and HTML outputs under the data directory.
4. The native shell starts server-side monitoring over SSH when the window opens.
5. Serve the dashboard JSON locally on the server and read it through the hidden SSH tunnel.
6. Stop server-side monitoring when the native shell window closes.

## Output Targets

- `data/state.json`
- `data/totals.json`
- `data/summary.json`
- `data/daily/YYYY-MM-DD.json`
- `data/reports/current_users.csv`
- `data/reports/current_users.md`
- `data/web/dashboard.json`
- `data/web/index.html`

## Config Knobs

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
- `bandwidth_limit_mbps`
- `network_interface`
- `wireguard_mtu`

## Access Notes

- Read `ACCESS_AND_DOCS.md` before looking for credentials or server access instructions.
- The external documentation folder contains the local access cheat sheets and secret-access notes.
- The launcher looks for SSH keys via `AUTOSTOPVPN_SSH_KEY` and `AUTOSTOPCRM_SSH_KEY`, then falls back to the standard names in `~/.ssh`.
- Keep private keys and passwords out of Git and out of mirrored repo copies unless you are performing a controlled recovery.

## Verification Checklist

- `.\audit_autostopvpn.ps1 -RunTests`
- `python -m unittest discover -s tests -v`
- `python .\amnezia_traffic_collector.py doctor`
- `python .\amnezia_traffic_collector.py status`
- confirm GitHub branch `autostopVPN` matches local files
- confirm the same VPN files are present on the server mirror

## Maintenance Rules

- Keep the maintenance pass staged: local verification, GitHub branch sync, then a separately confirmed server mirror sync.
- Delete only proven junk: ignored caches, generated runtime output, duplicate docs, or files replaced by a verified equivalent.
- Treat `apply_telegram_keepalive_fix.ps1` as a high-risk helper because it changes live WireGuard peer keepalive and the live container config; run `-DryRun` or `-WhatIf` first and keep the backup path for rollback.
- Treat `apply_telegram_mtu_fix.ps1` as a high-risk helper because it changes live container MTU; run `-DryRun` or `-WhatIf` before applying.
- Treat `apply_telegram_mss_fallback.ps1` as a high-risk helper because it changes live container firewall rules and the container start script; run `-DryRun -NoRestart` or `-WhatIf -NoRestart` before applying and keep the `start.sh.mss.bak.<timestamp>` backup path.
- Keep large refactors incremental. Preserve the dashboard JSON shape and the native shell refresh contract.

## Safe Change Rule

Prefer small edits in the collector, run tests immediately, then sync the same VPN files to GitHub and the server mirror. Avoid changing CRM-only files unless the user explicitly asks for mirrored deployment work.
