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

    def test_network_check_has_read_only_telegram_diagnostics(self) -> None:
        text = (ROOT / "check_autostopvpn_network.ps1").read_text(encoding="utf-8")
        self.assertIn("Telegram mobile readiness", text)
        self.assertIn("Peer keepalive summary", text)
        self.assertIn("Telegram MSS counters", text)
        self.assertIn("Gateway jitter", text)
        self.assertIn("no_iptables_changes=true", text)
        self.assertIn("PersistentKeepalive", text)
        self.assertNotIn("ip link set", text)
        self.assertNotIn("wg set", text)
        self.assertNotIn("docker restart", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("iptables -A", text)
        self.assertNotIn("iptables -I", text)
        self.assertNotIn("iptables -D", text)

    def test_mss_fallback_helper_has_safety_guards(self) -> None:
        text = (ROOT / "apply_telegram_mss_fallback.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", text)
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("[switch]$NoRestart", text)
        self.assertIn("Resolve-SshKey", text)
        self.assertIn("AUTOSTOPVPN_SSH_KEY", text)
        self.assertIn("AUTOSTOPCRM_SSH_KEY", text)
        self.assertIn("Invoke-GuardedNativeCommand", text)
        self.assertIn("TCPMSS", text)
        self.assertIn("--set-mss", text)
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
        self.assertIn("Apply Telegram MSS fallback 1240", output)


if __name__ == "__main__":
    unittest.main()
