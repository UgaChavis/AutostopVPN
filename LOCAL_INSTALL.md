# Local Install

This repository is intended to be used as a source tree and as a local installation package.

## Recommended local install path

`%LOCALAPPDATA%\AutostopVPN`

The install script copies the project there and creates a desktop shortcut named `Autostop VPN` with a custom VPN shield icon.

## Install

```powershell
.\install_autostopvpn.ps1
```

## Launch

Use the desktop shortcut or run:

```powershell
.\start_autostopvpn.ps1
```

## Diagnostics

Show the latest cached state:

```powershell
python .\amnezia_traffic_collector.py status
```

Check local prerequisites:

```powershell
python .\amnezia_traffic_collector.py doctor
```

Run the local/server recovery wrapper after a VPN stability incident:

```powershell
.\scheduled_recovery_checks.ps1 -PingCount 30 -SampleSeconds 30 -LocalDownloadBytes 10485760
```

If it reports `AmneziaVPN` MTU `1376`, open PowerShell as Administrator and repair only the local tunnel MTU:

```powershell
.\repair_local_amnezia_mtu.ps1 -DryRun
.\repair_local_amnezia_mtu.ps1
```

## Remove

```powershell
.\remove_autostopvpn.ps1
```

## Notes

- The desktop shortcut launches `start_autostopvpn.ps1`.
- The default monitoring SSH target is MNG 1 Manager at `46.8.254.189`.
- Use `open_amnezia_dashboard.ps1 -HostName <host>` or the Python shell's
  `--host <host>` option to select a different monitoring server explicitly.
- The native shell now defaults to a 1 second refresh interval and an operations cockpit visual theme.
- The install script also generates `AutostopVPN.ico` inside the installed copy and assigns it to the shortcut.
- The launcher then opens the native shell window directly and keeps the SSH tunnel hidden.
- The shell starts the server-side collector/dashboard services when it opens and stops them when it closes.
- If your SSH key is stored under a custom path, set `AUTOSTOPVPN_SSH_KEY` or `AUTOSTOPCRM_SSH_KEY` before launching.
- The collector and dashboard services should not be enabled for continuous boot-time autostart unless you intentionally want always-on monitoring.
- Reinstall preserves `%LOCALAPPDATA%\AutostopVPN\logs` and `%LOCALAPPDATA%\AutostopVPN\secret-backups` so recovery logs and local MTU rollback backups are not removed.
