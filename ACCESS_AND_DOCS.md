# Autostop VPN Access And Docs

This file is the quick orientation sheet for the VPN workspace and for the external access documentation.

## Start Here

1. Read [CODEX_PROJECT_MAP.md](CODEX_PROJECT_MAP.md)
2. Read [README.md](README.md)
3. Read [LOCAL_INSTALL.md](LOCAL_INSTALL.md)
4. Read [AMNEZIA_VPN_MONITORING.md](AMNEZIA_VPN_MONITORING.md)
5. Read [AMNEZIA_FULL_ACCESS_RECOVERY.md](AMNEZIA_FULL_ACCESS_RECOVERY.md) before restoring owner access in AmneziaVPN.

## External Documentation Root

The shared access notes live outside this Git repository. On this workstation the current mounted copy is:

- `%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер`

An older Google Drive path may exist on other machines:

- `%USERPROFILE%\Мой диск\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM`

Useful files in that folder:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `AUTOSTOPCRM_SERVER_ACCESS.txt`
- `СЕКРЕТНЫЙ_ДОСТУП_AUTOSTOP_CRM.txt`
- `.ssh\autostopcrm_server_ed25519`
- `PROJECT_HANDOFF.md`
- `MASTER-PLAN.md`
- `README.md`

## Where To Look For SSH Access

Preferred local SSH key paths:

- `%USERPROFILE%\.ssh\autostopvpn_server_ed25519`
- `%USERPROFILE%\.ssh\autostopcrm_server_ed25519`
- `%USERPROFILE%\.ssh\codex_autostopcrm`
- `%USERPROFILE%\.ssh\codex_autostopcrm_key`
- `%USERPROFILE%\.ssh\codex_autostopvpn`
- `%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\.ssh\autostopcrm_server_ed25519`

Environment variable overrides:

- `AUTOSTOPVPN_SSH_KEY`
- `AUTOSTOPCRM_SSH_KEY`

The shell launcher also falls back to these names in `~/.ssh`:

- `autostopvpn_server_ed25519`
- `autostopcrm_server_ed25519`
- `codex_autostopvpn`
- `codex_autostopcrm`
- `codex_autostopcrm_key`

## What Not To Do

- Do not copy private keys or passwords into the repository.
- Do not commit the external documentation folder.
- Do not mirror secret material into the shared CRM repo unless there is a specific recovery reason.

## Practical Launch Path

For the Windows desktop flow:

1. Run `install_autostopvpn.ps1`
2. Launch `Autostop VPN.lnk` from the desktop
3. If the launcher cannot find a key, set `AUTOSTOPVPN_SSH_KEY` or `AUTOSTOPCRM_SSH_KEY` and retry

For outage monitoring without touching the VPN runtime:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 10 -SampleSeconds 15
```

For owner/full-access recovery in the AmneziaVPN app, follow [AMNEZIA_FULL_ACCESS_RECOVERY.md](AMNEZIA_FULL_ACCESS_RECOVERY.md). Do not copy the SSH private key or any `vpn://...` full-access key into this repository.

## Repository Focus

This repository contains the VPN monitoring and shell workspace only.

- telemetry collection
- dashboard generation
- local shell launcher
- Windows install and uninstall helpers
- tests and runbooks

