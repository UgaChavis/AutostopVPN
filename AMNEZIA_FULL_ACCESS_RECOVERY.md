# AmneziaVPN Full Access Recovery

This runbook restores owner/full-access control in the AmneziaVPN app after the local application was removed or reset.

## Current Known Server Data

- Server: `crm.autostopcrm.ru`
- Public IP: `46.8.254.243`
- SSH user: `root`
- SSH port: `22`
- Provider hostname: `vps26457.mnogoweb.in`
- VPN container: `amnezia-awg2`
- VPN interface: `awg0`
- Live Amnezia config inside the container: `/opt/amnezia/awg/awg0.conf`
- Main VPN endpoint: `46.8.254.243:47895/udp`
- Mobile/current profile endpoint: `46.8.254.243:443/udp`

The SSH private key is not stored in this repository. Use the external access bundle:

```text
%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\.ssh\autostopcrm_server_ed25519
```

The server access notes are in:

```text
%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\AUTOSTOPCRM_SERVER_ACCESS.txt
%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\СЕКРЕТНЫЙ_ДОСТУП_AUTOSTOP_CRM.txt
```

Do not paste private keys, `vpn://...` keys, passwords, or exported `.vpn` files into Git, chats, tickets, or this repository.

## Safety Rule

For this production server, use the recovery path for an already configured Amnezia server. Do not rebuild, reinstall, clear, or remove Amnezia services from the server just to restore the local app.

The live VPN peer config is inside the running container filesystem. Recreating `amnezia-awg2` can desynchronize or lose peers.

## Step 1: Verify The App Is Installed

On this PC, AmneziaVPN is currently installed here:

```text
C:\Program Files\AmneziaVPN\AmneziaVPN.exe
```

The installed version seen on 2026-06-19 is `4.8.19.0`.

If the app is missing, download it from the official page:

```text
https://amnezia.org/downloads
```

Use the Windows installer. Do not import random keys from public sources.

## Step 2: Verify SSH Access Before Opening Amnezia

Open PowerShell and run:

```powershell
ssh -i "%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\.ssh\autostopcrm_server_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "docker ps --format '{{.Names}} {{.Status}}' | grep amnezia-awg2"
```

Expected result: a line for `amnezia-awg2` with `Up ...`.

If this fails, do not continue in AmneziaVPN. Fix SSH access first, because Amnezia full access depends on SSH.

## Step 3: Add The Existing Server In AmneziaVPN

1. Open AmneziaVPN.
2. Press the plus button or `Get Started`.
3. Select `Self-hosted VPN`.
4. Enter the server:

```text
crm.autostopcrm.ru
```

If the app asks for host and port separately:

```text
Host: crm.autostopcrm.ru
Port: 22
```

5. Enter SSH username:

```text
root
```

6. Choose SSH private key authentication.
7. Select or paste the private key from:

```text
%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\.ssh\autostopcrm_server_ed25519
```

If Amnezia asks for the key text, open the file locally and copy the whole key, including the `BEGIN` and `END` lines. Do not save a copy inside this repository.

## Step 4: Use Skip Setup

When AmneziaVPN asks how to set up the server, choose `Skip setup` if that option is shown.

This is the important recovery step: the server already has Amnezia installed and running. Skipping setup creates a local management connection without reinstalling protocols.

After the connection is created:

1. Click the connection/server name.
2. Open the gear icon next to the server connection.
3. Open the `Management` tab.
4. Click `Check the server for previously installed Amnezia services`.
5. Wait until Amnezia refreshes the existing protocols and services.

If Amnezia shows the existing AmneziaWG protocol after the scan, full-access recovery succeeded.

## Step 5: Verify The Existing VPN Settings

In AmneziaVPN, confirm that the server/protocol corresponds to the existing deployment:

```text
Server/IP: 46.8.254.243 or crm.autostopcrm.ru
Protocol: AmneziaWG / AmneziaWG Legacy, depending on how the app labels the existing installation
Main listener: 47895/udp
Current mobile endpoint: 443/udp
```

Do not install a new protocol only because the UI offers it. First confirm the existing services are detected.

## Step 6: Create New User Access

For normal users, do not share full access. Use `Share VPN Access` / guest access.

Recommended guest profile values for this server:

```ini
Endpoint = 46.8.254.243:443
MTU = 1280
PersistentKeepalive = 25
```

Rules:

- one user/device gets one separate profile
- do not reuse the same `.conf` or `.vpn` for multiple people
- full access is only for owner/admin devices
- if a guest profile leaks, revoke that guest profile in AmneziaVPN and create a new one

## Step 7: Verify After Recovery

From the AutostopVPN workspace, run:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 10 -SampleSeconds 15
```

Expected critical signals:

- `amnezia-awg2` is `Up`
- UDP `47895` is published
- UDP `443` forward is active
- `server_awg0_mtu=1280 ok=true`
- `server_config_mtu=1280 ok=true`
- `keepalive_off=0`
- Telegram HTTPS is OK
- OpenAI HTTPS is OK
- critical pings show `0%` packet loss

For the local Windows tunnel, run:

```powershell
.\scheduled_recovery_checks.ps1 -PingCount 10 -SampleSeconds 5 -LocalDownloadBytes 5242880
```

The local `AmneziaVPN` IPv4 and IPv6 interfaces should show MTU `1280`.

## If The App Shows Duplicate Servers

After full-access recovery, AmneziaVPN can show two local server cards for the same IP. This does not automatically mean the production VPN was duplicated.

Latest production check on 2026-06-19 showed one running VPN container, `amnezia-awg2`, and `67` configured peers. The duplicate cards were local AmneziaVPN app records for the same server IP, not two production VPN servers.

Before deleting anything:

1. Confirm the production server still has only one VPN container:

```powershell
ssh -i "%USERPROFILE%\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер\.ssh\autostopcrm_server_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep amnezia"
```

Expected result: only `amnezia-awg2`.

2. Back up the local Amnezia server list before UI cleanup:

```powershell
$backupDir = Join-Path $env:LOCALAPPDATA 'AutostopVPN\secret-backups'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
reg export 'HKCU\Software\AmneziaVPN.ORG\AmneziaVPN\Servers' (Join-Path $backupDir "AmneziaVPN-servers-before-dedup-$(Get-Date -Format 'yyyyMMdd-HHmmss').reg") /y
```

3. In AmneziaVPN, keep the active full-access card that shows the existing server `46.8.254.243` and `AmneziaWG (версия 2)`.
4. Remove the duplicate inactive local card only if Amnezia offers a local removal option.
5. If the confirmation mentions removing software, clearing services, deleting the server, or uninstalling Amnezia from the server, cancel.

Safe wording is local-only removal from the app. Unsafe wording is server cleanup or removal of installed services.

After cleanup, rerun:

```powershell
.\check_autostopvpn_network.ps1 -PingCount 10 -SampleSeconds 15
```

## What Not To Press

Avoid these actions during full-access recovery unless you have a fresh backup and a planned maintenance window:

- remove server from Amnezia with cleanup on the server
- clear Amnezia software from the server
- reinstall AmneziaWG over the existing container
- recreate or restart `amnezia-awg2` as a first diagnostic step
- edit `/opt/amnezia/awg/awg0.conf` manually
- share full access with ordinary users

## Official References

- Amnezia download page: `https://amnezia.org/downloads`
- Set up self-hosted VPN: `https://docs.amnezia.org/documentation/instructions/install-vpn-on-server/`
- Share full access: `https://docs.amnezia.org/documentation/instructions/share-full-access/`
- Connect over SSH: `https://docs.amnezia.org/documentation/instructions/connect-via-ssh/`
- Connect with text key: `https://docs.amnezia.org/documentation/instructions/connect-via-text-key/`
