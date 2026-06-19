# AutostopVPN Project Map

This file is the project orientation sheet for Codex and maintainers.

Last verified from the live server on 2026-06-19.

## Canonical Locations

- Local workspace: `%USERPROFILE%\Desktop\AutostopVPN`
- Local install target: `%LOCALAPPDATA%\AutostopVPN`
- Desktop launcher: `%USERPROFILE%\Desktop\Autostop VPN.lnk`
- GitHub VPN branch: `autostopVPN`
- Current live VPN mirror path checked on the server: `/root/AutostopVPN/repo`
- Older expected CRM mirror path: `/opt/autostopcrm`; latest check did not find the collector script there
- Current external access documentation root: `%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер`
- Older external access documentation root: `%USERPROFILE%\Мой диск\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM`

## Main Files

- `amnezia_traffic_collector.py`: collector, report builder, `collect`, `report`, `status`, `doctor`
- `amnezia_vpn_shell.py`: native desktop shell that displays the live snapshot with the operations cockpit theme and faster refresh cadence
- `amnezia_dashboard_server.py`: request-time dashboard server for fresh page loads
- collector tracks current channel utilization, daily average load, and daily peak bandwidth
- the dashboard shows a top traffic banner with load, headroom, and risk state
- `amnezia_server_info.json`: server metadata, SSH details, bandwidth limit, notes
- `open_amnezia_dashboard.ps1` and `open_amnezia_dashboard.cmd`: Windows shell launchers
- `check_autostopvpn_network.ps1`: read-only outage monitor for SSH, provider loss, WireGuard handshakes, alternate UDP endpoint state, Cloudflare/Google egress comparison, Telegram mobile readiness, Telegram/OpenAI HTTPS availability, MSS counters, Telegram relay state, gateway jitter, and traffic deltas
- `scheduled_recovery_checks.ps1`: logged Windows recovery check wrapper for local Amnezia MTU, Cloudflare/Telegram packet loss, non-critical Google route warnings, Telegram/OpenAI HTTPS, local download speed, and the server read-only monitor
- `repair_local_amnezia_mtu.ps1`: elevated Windows-only helper for backing up, applying, and rolling back the local Amnezia tunnel MTU when the client returns to `1376`
- `apply_udp443_forward.ps1`: high-risk helper for adding or rolling back the host UDP `443` DNAT forward to the existing `47895/udp` VPN listener without restarting `amnezia-awg2`
- `apply_telegram_keepalive_fix.ps1`: high-risk helper for applying `PersistentKeepalive=25` to all peers with `-DryRun`, `-WhatIf`, and rollback support
- `apply_telegram_mtu_fix.ps1`: high-risk helper for live `awg0` MTU changes with `-DryRun` and `-WhatIf`
- `apply_telegram_mss_fallback.ps1`: high-risk helper for post-keepalive generic TCP MSS fallback with `-DryRun`, `-WhatIf`, `-NoRestart`, and rollback support
- `apply_telegram_ipv6_relay_fix.ps1`: high-risk helper for the current-VPS Telegram IPv4 to IPv6 relay with `-DryRun`, `-WhatIf`, and rollback support
- `audit_autostopvpn.ps1`: read-only maintenance audit for status, size hotspots, stale markers, hard-coded paths, ignored artifacts, and tests
- current Telegram fallback state is server keepalive on all peers, `awg0` MTU `1280`, generic `awg0` TCP MSS `1240`, stable `47895/udp`, alternate mobile endpoint `443/udp`, and a current-VPS Telegram IPv4 to IPv6 relay for blocked provider routes
- current provider observation: gateway RTT is stable with `0%` packet loss in the latest read-only sample; occasional RTT spikes above `100 ms` remain a known provider pattern
- current local Windows state: on 2026-06-19 the active `AmneziaVPN` IPv4/IPv6 interfaces and persistent service ImagePath were verified at MTU `1280`; use `repair_local_amnezia_mtu.ps1` only if a future check reports `1376`
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
- `AMNEZIA_FULL_ACCESS_RECOVERY.md`: owner/full-access recovery guide for restoring AmneziaVPN app management without reinstalling the live server

## Current Live Baseline

Verified on 2026-06-19:

- `amnezia-awg2` is running and has been up for about 8 days.
- UDP `47895` is published and listening.
- UDP `443` DNAT is active and forwards to the existing `47895/udp` listener.
- `67` peers are configured; `67` have server-side `PersistentKeepalive=25`.
- Latest collector status sample: `28` active peers in the 180 second window, channel flow about `797.79 KiB/s`, utilization about `0.65%` of the configured 1 Gbps limit, collector warnings `0`.
- Live/config `awg0` MTU is `1280`; generic TCP MSS clamp is `1240`.
- Telegram and OpenAI HTTPS checks pass; critical packet loss is `0%`.
- Provider gateway jitter can appear without packet loss and should not trigger VPN container restarts by itself.
- Local Windows `AmneziaVPN` IPv4/IPv6 interface and persistent service ImagePath are currently at MTU `1280`.
- Latest local recovery check: `Health failures: 0`; one warning for local VPN download `19.13 Mbps` below the `20 Mbps` warning threshold. A server-side Cloudflare sample reached about `42.7 Mbps`, so the warning is local/client-route scoped.
- No Windows Task Scheduler jobs matching `Autostop` or `VPN` were registered on this workstation during the 2026-06-19 check; run `scheduled_recovery_checks.ps1` manually until recurring jobs are recreated.

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
- Treat `apply_udp443_forward.ps1` as a high-risk helper because it changes host NAT and installs a systemd oneshot service; run `-DryRun` or `-WhatIf` first and use `-Rollback` to remove only the UDP `443` forward.
- Treat `apply_telegram_mtu_fix.ps1` as a high-risk helper because it changes live container MTU; run `-DryRun` or `-WhatIf` before applying.
- Treat `apply_telegram_mss_fallback.ps1` as a high-risk helper because it changes live container firewall rules and the container start script; run `-DryRun -NoRestart` or `-WhatIf -NoRestart` before applying and keep the `start.sh.mss.bak.<timestamp>` backup path.
- Treat `apply_telegram_ipv6_relay_fix.ps1` as a high-risk helper because it changes host systemd and iptables routing for Telegram destinations; run `-DryRun` or `-WhatIf` first and use `-Rollback` to remove only the relay.
- Keep large refactors incremental. Preserve the dashboard JSON shape and the native shell refresh contract.

## Safe Change Rule

Prefer small edits in the collector, run tests immediately, then sync the same VPN files to GitHub and the server mirror. Avoid changing CRM-only files unless the user explicitly asks for mirrored deployment work.
