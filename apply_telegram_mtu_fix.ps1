$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverInfoPath = Join-Path $projectRoot "amnezia_server_info.json"
$serverInfo = Get-Content $serverInfoPath -Raw | ConvertFrom-Json

$sshKey = Join-Path $HOME ".ssh\codex_autostopcrm"
$sshPort = [int]$serverInfo.ssh_port
$sshUser = [string]$serverInfo.ssh_user
$sshHost = [string]$serverInfo.public_ip
$sshDestination = "${sshUser}@${sshHost}"
$container = [string]$serverInfo.vpn_container
$mtu = if ($serverInfo.wireguard_mtu) { [int]$serverInfo.wireguard_mtu } else { 1380 }
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$backupCommand = "mkdir -p /root/autostopvpn-backups && docker cp ${container}:/opt/amnezia/awg/awg0.conf /root/autostopvpn-backups/awg0.conf.bak.$timestamp"
$scpDestination = "${sshDestination}:/var/lib/amnezia-traffic/server_info.json"

Write-Host "Applying MTU $mtu on ${sshDestination}:$sshPort ..."
& ssh -i $sshKey -p $sshPort $sshDestination $backupCommand
& ssh -i $sshKey -p $sshPort $sshDestination "docker exec ${container} ip link set dev awg0 mtu $mtu"

Write-Host "Syncing server metadata to /var/lib/amnezia-traffic/server_info.json ..."
& scp -i $sshKey -P $sshPort $serverInfoPath $scpDestination

Write-Host "MTU fix applied and server metadata synced."
