from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MaintenanceArtifactTests(unittest.TestCase):
    def test_docs_use_portable_user_paths(self) -> None:
        docs = ["CODEX_PROJECT_MAP.md", "ACCESS_AND_DOCS.md"]
        for name in docs:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\User", text)
            self.assertNotIn("C:\\Users\\9860606", text)

    def test_maintenance_audit_is_documented(self) -> None:
        self.assertTrue((ROOT / "MAINTENANCE.md").exists())
        self.assertTrue((ROOT / "audit_autostopvpn.ps1").exists())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        project_map = (ROOT / "CODEX_PROJECT_MAP.md").read_text(encoding="utf-8")
        self.assertIn("audit_autostopvpn.ps1", readme)
        self.assertIn("MAINTENANCE.md", project_map)

    def test_installer_preserves_local_logs_and_secret_backups(self) -> None:
        text = (ROOT / "install_autostopvpn.ps1").read_text(encoding="utf-8")
        self.assertIn("Backup-LocalInstallState", text)
        self.assertIn("Restore-LocalInstallState", text)
        self.assertIn('"logs", "secret-backups"', text)
        self.assertIn("AutostopVPN-install-preserve-", text)
        self.assertIn("Restore-LocalInstallState -InstallRoot $installRoot", text)

    def test_collector_service_does_not_depend_on_script_shebang(self) -> None:
        text = (ROOT / "amnezia-traffic-collector.service").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/usr/bin/python3 /usr/local/bin/amnezia_traffic_collector.py collect",
            text,
        )
        self.assertNotIn("ExecStart=/usr/local/bin/amnezia_traffic_collector.py collect", text)

    def test_mtu_helper_has_safety_guards_and_shared_key_resolution(self) -> None:
        text = (ROOT / "apply_telegram_mtu_fix.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("Invoke-GuardedNativeCommand", text)
        self.assertNotIn('$sshKey = Join-Path $HOME ".ssh\\codex_autostopcrm"', text)

    def test_mtu_helper_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_telegram_mtu_fix.ps1"),
                "-DryRun",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-Container",
                "amnezia-awg2",
                "-Mtu",
                "1280",
                "-NoServerInfoSync",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("Backup live awg0.conf", output)
        self.assertIn("Apply awg0 MTU 1280", output)

    def test_local_amnezia_mtu_repair_helper_has_safety_guards(self) -> None:
        text = (ROOT / "repair_local_amnezia_mtu.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("[switch]$Rollback", text)
        self.assertIn("Test-IsElevated", text)
        self.assertIn("secret-backups", text)
        self.assertIn("reg export", text)
        self.assertIn("reg import", text)
        self.assertIn("netsh interface ipv4 set subinterface", text)
        self.assertIn("netsh interface ipv6 set subinterface", text)
        self.assertIn("no_server_changes=true", text)
        self.assertIn("no_peer_changes=true", text)
        self.assertIn("no_iptables_changes=true", text)
        self.assertIn("no_systemd_changes=true", text)
        self.assertNotIn("ssh ", text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("iptables -", text)
        self.assertNotIn("wg set", text)

    def test_local_amnezia_mtu_repair_helper_dry_run_does_not_require_elevation(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        command = [
            powershell,
            "-NoProfile",
            "-File",
            str(ROOT / "repair_local_amnezia_mtu.ps1"),
            "-DryRun",
        ]
        if Path(powershell).name.lower().startswith("powershell"):
            command[2:2] = ["-ExecutionPolicy", "Bypass"]

        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("planned_action=apply", output)
        self.assertIn("planned_active_mtu=1280", output)
        self.assertIn("no_server_changes=true", output)

    def test_network_check_has_read_only_telegram_diagnostics(self) -> None:
        text = (ROOT / "check_autostopvpn_network.ps1").read_text(encoding="utf-8")
        self.assertIn("Telegram mobile readiness", text)
        self.assertIn("Peer keepalive summary", text)
        self.assertIn("Telegram MSS counters", text)
        self.assertIn("telegram_api_https_ok=true", text)
        self.assertIn("telegram_api_https_attempt=", text)
        self.assertIn("OpenAI API availability", text)
        self.assertIn("openai_api_https_ok=true", text)
        self.assertIn("chatgpt_https_reachable=true", text)
        self.assertIn("PingIntervalSeconds", text)
        self.assertIn("$script:PingIntervalSeconds", text)
        self.assertIn("SshCommandTimeoutSeconds", text)
        self.assertIn("WaitForExit", text)
        self.assertIn("Kill($true)", text)
        self.assertNotIn("-i 0.2", text)
        self.assertIn("Telegram IPv4 to IPv6 relay state", text)
        self.assertIn("Alternate UDP endpoint", text)
        self.assertIn("normalizedScript", text)
        self.assertIn("autostopvpn-telegram-relay.service", text)
        self.assertIn("AlternateUdpPort", text)
        self.assertIn("udp_${alternate_port}_forward_present", text)
        self.assertIn("udp_${target_port}_published", text)
        self.assertIn("curl -4 -sS --connect-timeout 10 --max-time", text)
        self.assertNotIn("curl -4 -sS -L --max-time", text)
        self.assertIn("'$4 ~ suffix", text)
        self.assertIn("Gateway jitter", text)
        self.assertIn("no_iptables_changes=true", text)
        self.assertIn("PersistentKeepalive", text)
        self.assertIn("mobile_profile_endpoint", text)
        self.assertIn("mobile_profile_required", text)
        self.assertIn("normalizedScript", text)
        self.assertIn('$Script -replace "`r`n", "`n"', text)
        self.assertNotIn("pilot_profile_required", text)
        self.assertNotIn("ip link set", text)
        self.assertNotIn("wg set", text)
        self.assertNotIn("docker restart", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("iptables -A", text)
        self.assertNotIn("iptables -I", text)
        self.assertNotIn("iptables -D", text)

    def test_udp443_forward_helper_has_safety_guards_and_rollback(self) -> None:
        text = (ROOT / "apply_udp443_forward.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("[switch]$Rollback", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("Invoke-GuardedNativeCommand", text)
        self.assertIn("ConvertTo-RemoteCommand", text)
        self.assertIn("normalizedScript", text)
        self.assertIn("iptables-nft", text)
        self.assertIn("-t nat -C PREROUTING", text)
        self.assertIn("--dport", text)
        self.assertIn("--to-destination", text)
        self.assertIn("autostopvpn-udp443-forward.service", text)
        self.assertIn("systemctl enable --now", text)
        self.assertIn("systemctl disable --now", text)
        self.assertIn("PartOf=docker.service", text)
        self.assertIn("'$4 ~ suffix", text)
        self.assertIn("Rollback UDP", text)
        self.assertIn("key_resolved=true", text)
        self.assertNotIn("PrivateKey", text)
        self.assertNotIn("docker restart", text)
        self.assertNotIn("systemctl restart", text)

    def test_udp443_forward_helper_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_udp443_forward.ps1"),
                "-DryRun",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-Container",
                "amnezia-awg2",
                "-DockerNetwork",
                "amnezia-dns-net",
                "-ListenPort",
                "443",
                "-TargetPort",
                "47895",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("mode=apply", output)
        self.assertIn("Apply UDP 443 forward", output)
        self.assertIn("key_resolved=true", output)

    def test_udp443_forward_helper_rollback_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_udp443_forward.ps1"),
                "-DryRun",
                "-Rollback",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-Container",
                "amnezia-awg2",
                "-DockerNetwork",
                "amnezia-dns-net",
                "-ListenPort",
                "443",
                "-TargetPort",
                "47895",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("mode=rollback", output)
        self.assertIn("Rollback UDP 443 forward", output)

    def test_mss_fallback_helper_has_safety_guards(self) -> None:
        text = (ROOT / "apply_telegram_mss_fallback.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("[switch]$NoRestart", text)
        self.assertIn("RollbackBackupPath", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("Invoke-GuardedNativeCommand", text)
        self.assertIn("TCPMSS", text)
        self.assertIn("--set-mss", text)
        self.assertIn("docker exec -i", text)
        self.assertIn("normalizedScript", text)
        self.assertIn("start.sh.mss.bak", text)
        self.assertIn("Rollback Telegram MSS fallback", text)
        self.assertIn("container start script", text)
        self.assertIn("tail[[:space:]]+-f", text)
        self.assertNotIn("docker restart", text)
        self.assertNotIn("systemctl restart", text)

    def test_mss_fallback_helper_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_telegram_mss_fallback.ps1"),
                "-DryRun",
                "-NoRestart",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-Container",
                "amnezia-awg2",
                "-Interface",
                "awg0",
                "-Mss",
                "1240",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("no_restart=True", output)
        self.assertIn("mode=apply", output)
        self.assertIn("Apply Telegram MSS fallback 1240", output)

    def test_telegram_ipv6_relay_helper_has_safety_guards(self) -> None:
        text = (ROOT / "apply_telegram_ipv6_relay_fix.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("[switch]$Rollback", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("ConvertTo-RemoteCommand", text)
        self.assertIn("normalizedScript", text)
        self.assertIn("autostopvpn-telegram-relay.service", text)
        self.assertIn("AUTOSTOPVPN_TG_RELAY", text)
        self.assertIn("SO_ORIGINAL_DST", text)
        self.assertIn("149.154.166.110", text)
        self.assertIn("2001:67c:4e8:f004::9", text)
        self.assertIn("2001:67c:4e8:f002::a", text)
        self.assertIn("no_amnezia_restart=true", text)
        self.assertIn("no_peer_changes=true", text)
        self.assertIn("telegram_relay_removed=true", text)
        self.assertNotIn("EgressHostName", text)
        self.assertNotIn("wg-telegram-egress", text)
        self.assertNotIn("docker restart", text)

    def test_telegram_ipv6_relay_helper_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_telegram_ipv6_relay_fix.ps1"),
                "-DryRun",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-RelayPort",
                "10443",
                "-BridgeInterface",
                "amn0",
                "-ContainerBridgeIp",
                "172.29.172.2/32",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("mode=apply", output)
        self.assertIn("Apply Telegram IPv4 to IPv6 relay", output)
        self.assertIn("no_amnezia_restart=true", output)

    def test_keepalive_helper_has_safety_guards_and_rollback(self) -> None:
        text = (ROOT / "apply_telegram_keepalive_fix.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("RollbackBackupPath", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("Invoke-GuardedNativeCommand", text)
        self.assertIn("PersistentKeepalive", text)
        self.assertIn("persistent-keepalive", text)
        self.assertIn("docker exec -i", text)
        self.assertIn("normalizedScript", text)
        self.assertIn("awg0.conf.keepalive.bak", text)
        self.assertIn("Restore keepalive backup", text)
        self.assertNotIn("PrivateKey", text)
        self.assertNotIn("docker restart", text)
        self.assertNotIn("systemctl restart", text)

    def test_keepalive_helper_dry_run_does_not_require_ssh_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "dummy_key"
            key_path.write_text("dummy", encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-File",
                str(ROOT / "apply_telegram_keepalive_fix.ps1"),
                "-DryRun",
                "-HostName",
                "127.0.0.1",
                "-SshUser",
                "root",
                "-SshPort",
                "22",
                "-KeyPath",
                str(key_path),
                "-Container",
                "amnezia-awg2",
                "-Interface",
                "awg0",
                "-Keepalive",
                "25",
            ]
            if Path(powershell).name.lower().startswith("powershell"):
                command[2:2] = ["-ExecutionPolicy", "Bypass"]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("dry_run=True", output)
        self.assertIn("mode=apply", output)
        self.assertIn("Apply Telegram keepalive 25", output)

    def test_docs_classify_active_documents_and_avoid_stale_server_note(self) -> None:
        maintenance = (ROOT / "MAINTENANCE.md").read_text(encoding="utf-8")
        server_info = (ROOT / "amnezia_server_info.json").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Documentation Classification", maintenance)
        self.assertIn("no tracked Markdown delete candidates", maintenance)
        self.assertIn("Current Production Baseline", readme)
        self.assertIn("Telegram baseline", server_info)
        self.assertIn("apply_telegram_ipv6_relay_fix.ps1", readme)
        self.assertIn("repair_local_amnezia_mtu.ps1", readme)
        self.assertIn("repair_local_amnezia_mtu.ps1", maintenance)
        self.assertIn("Telegram current-VPS relay", server_info)
        self.assertNotIn("apply_telegram_egress_route.ps1", readme)
        self.assertNotIn("telegram_egress", server_info)
        self.assertNotIn("185.42.164.2", server_info)

    def test_scheduled_recovery_checks_use_current_read_only_monitor(self) -> None:
        text = (ROOT / "scheduled_recovery_checks.ps1").read_text(encoding="utf-8")
        self.assertIn("check_autostopvpn_network.ps1", text)
        self.assertIn("Get-NetIPInterface", text)
        self.assertIn("AmneziaVPN", text)
        self.assertIn("LocalDownloadBytes", text)
        self.assertIn("ServerPingIntervalSeconds", text)
        self.assertIn("MinLocalDownloadMbps", text)
        self.assertIn("WarnLocalDownloadMbps", text)
        self.assertIn("1.0.0.1", text)
        self.assertIn("-WarningOnly", text)
        self.assertIn("Invoke-LocalDownloadProbe", text)
        self.assertIn("local_download_mbps=", text)
        self.assertIn("speed_Bps=%{speed_download}", text)
        self.assertIn('Mozilla/5.0', text)
        self.assertIn("telegram_https attempt=$attempt http=(200|302)", text)
        self.assertNotIn("curl.exe -4 -sS -L --connect-timeout 10 --max-time 30", text)
        self.assertIn("api.telegram.org", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("$script:HealthFailures", text)
        self.assertIn("$script:HealthWarnings", text)
        self.assertIn("HEALTH_WARN", text)
        self.assertIn("Assert-PingHealthy", text)
        self.assertIn("Server monitor reported ${loss}% packet loss in", text)
        self.assertIn('if ($section -eq "Ping 8.8.8.8")', text)
        self.assertIn("telegram_api_https_ok=true", text)
        self.assertIn("-PingIntervalSeconds $ServerPingIntervalSeconds", text)
        self.assertIn("Invoke-TelegramHttpsProbe", text)
        self.assertIn("Invoke-OpenAiHttpsProbe", text)
        self.assertIn("openai_api_https attempt=$attempt http=(200|401|403)", text)
        self.assertIn("chatgpt_https http=(200|301|302|403)", text)
        self.assertIn("openai_api_https_ok=true", text)
        self.assertIn("chatgpt_https_reachable=true", text)
        self.assertIn("[regex]::Matches", text)
        self.assertIn("?<loss>", text)
        self.assertIn("?<lost>", text)
        self.assertNotIn('"0%\\s*loss"', text)
        self.assertNotIn('"0%\\s*потер"', text)
        self.assertNotIn("(?!.*0% packet loss)", text)
        self.assertIn("*>&1", text)
        self.assertIn("Health warnings:", text)
        self.assertIn("Health failures:", text)
        self.assertNotIn('throw "Provider gateway jitter warning is active."', text)
        self.assertIn("exit 1", text)
        self.assertNotIn("C:\\Users\\User", text)
        self.assertNotIn('Join-Path $env:USERPROFILE ".ssh\\codex_autostopcrm"', text)
        self.assertNotIn("id_ed25519", text)


if __name__ == "__main__":
    unittest.main()
