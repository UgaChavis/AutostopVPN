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

## Remove

```powershell
.\remove_autostopvpn.ps1
```

## Notes

- The desktop shortcut launches `start_autostopvpn.ps1`.
- The install script also generates `AutostopVPN.ico` inside the installed copy and assigns it to the shortcut.
- The launcher then opens the native shell window directly and keeps the SSH tunnel hidden.
- If your SSH key is stored under a custom path, set `AUTOSTOPVPN_SSH_KEY` or `AUTOSTOPCRM_SSH_KEY` before launching.
- The collector and dashboard services still run on the server.
