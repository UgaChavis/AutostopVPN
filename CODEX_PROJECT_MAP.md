# AutostopVPN Project Map

This file is the project orientation sheet for Codex and maintainers.

## Canonical Locations

- Local workspace: `C:\Users\9860606\Desktop\AutostopVPN`
- Local install target: `%LOCALAPPDATA%\AutostopVPN`
- Desktop launcher: `C:\Users\9860606\Desktop\Autostop VPN.lnk`
- GitHub VPN branch: `autostopVPN`
- Server mirror path: `/opt/autostopcrm`

## Main Files

- `amnezia_traffic_collector.py`: collector, report builder, `collect`, `report`, `status`, `doctor`
- collector tracks current channel utilization, daily average load, and daily peak bandwidth
- `amnezia_server_info.json`: server metadata, SSH details, bandwidth limit, notes
- `open_amnezia_dashboard.ps1` and `open_amnezia_dashboard.cmd`: Windows dashboard launchers
- `start_autostopvpn.ps1`: stable desktop entrypoint
- `install_autostopvpn.ps1`: local install and desktop shortcut creation
- `remove_autostopvpn.ps1`: local uninstall
- `AMNEZIA_VPN_MONITORING.md`: operator runbook
- `LOCAL_INSTALL.md`: local install guide
- `tests/test_amnezia_traffic_collector.py`: logic tests

## Runtime Flow

1. Read peer and server state from Docker and host probes.
2. Build current peer rows and traffic deltas.
3. Write JSON, CSV, MD, and HTML outputs under the data directory.
4. Serve the dashboard locally on the server.
5. Open the dashboard from Windows through SSH.

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
- `bandwidth_limit_mbps`
- `network_interface`

## Verification Checklist

- `python -m unittest discover -s tests -v`
- `python .\amnezia_traffic_collector.py doctor`
- `python .\amnezia_traffic_collector.py status`
- confirm GitHub branch `autostopVPN` matches local files
- confirm the same VPN files are present on the server mirror

## Safe Change Rule

Prefer small edits in the collector, run tests immediately, then sync the same VPN files to GitHub and the server mirror. Avoid changing CRM-only files unless the user explicitly asks for mirrored deployment work.
