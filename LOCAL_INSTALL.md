# Local Install

This repository is intended to be used as a source tree and as a local installation package.

## Recommended local install path

`%LOCALAPPDATA%\AutostopVPN`

The install script copies the project there and creates a desktop shortcut named `Autostop VPN`.

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
- The launcher then opens the SSH tunnel and dashboard.
- The collector and dashboard services still run on the server.
