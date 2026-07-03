# AutostopVPN Maintenance

This checklist keeps the VPN monitoring workspace clean without changing the VPN runtime or peer configuration.

## Baseline Audit

Run the read-only audit before cleanup or optimization work:

```powershell
.\audit_autostopvpn.ps1 -RunTests
```

The audit records:

- Git status and tracked-file size hotspots
- largest Python functions/classes
- stale cleanup markers and secret-shaped strings
- hard-coded user paths in documentation
- ignored runtime artifacts such as `__pycache__`, `.pytest_cache`, and `data`
- the unit test suite when `-RunTests` is passed

## Cleanup Policy

- Delete only proven junk: generated caches, ignored runtime output, duplicate docs, or files replaced by a verified equivalent.
- Do not delete a tracked file just because it looks old; first prove that no launcher, doc, service unit, or deployment step references it.
- Keep private keys, passwords, access notes, runtime data, and generated logs out of Git.
- Keep `README.md`, `CODEX_PROJECT_MAP.md`, `LOCAL_INSTALL.md`, `AMNEZIA_VPN_MONITORING.md`, `ACCESS_AND_DOCS.md`, and `AMNEZIA_FULL_ACCESS_RECOVERY.md` aligned when behavior changes.

## Documentation Classification

- `README.md`: active entrypoint. Keep concise and link to deeper runbooks instead of duplicating every operational step.
- `CODEX_PROJECT_MAP.md`: active maintainer map. Keep current file roles, runtime flow, and verification targets here.
- `AMNEZIA_VPN_MONITORING.md`: active production runbook. Keep live health baseline, Telegram tuning, deployment, and rollback here.
- `AMNEZIA_FULL_ACCESS_RECOVERY.md`: active owner/full-access recovery guide. Keep local app reinstall, SSH recovery, and safe Amnezia full-access steps here without storing secrets.
- `LOCAL_INSTALL.md`: active Windows install guide. Keep only desktop install, launch, diagnostics, and uninstall steps here.
- `ACCESS_AND_DOCS.md`: active pointer to external access notes. Do not copy secrets or full external docs into this repo.
- `MAINTENANCE.md`: active cleanup checklist. Keep deletion criteria and regression gates here.

There are currently no tracked Markdown delete candidates in this VPN workspace. If a new document appears, classify it as active, generated, historical, duplicate, or delete candidate before keeping it.

## Safe Optimization Order

1. Run the baseline audit and `python -m unittest discover -s tests -v`.
2. Make one small change package at a time: docs, script safety, collector internals, shell internals, performance, or tests.
3. Preserve these public contracts: `collect/report/status/doctor`, dashboard JSON shape, `%LOCALAPPDATA%\AutostopVPN`, desktop shortcut flow, systemd unit names, and `127.0.0.1:18080/dashboard.json`.
4. Keep refresh-loop work light: geo and MTU probes must stay cached, and the shell should keep the repeated-snapshot fast path.
5. After local verification, sync to the GitHub `autostopVPN` branch first; server mirror updates under `/opt/autostopcrm` are a separate confirmed rollout step.
6. Keep `%LOCALAPPDATA%\AutostopVPN\logs` and `%LOCALAPPDATA%\AutostopVPN\secret-backups` across local reinstall; the installer preserves these directories because they contain recovery evidence and local registry rollback files.

## High-Risk Helpers

`apply_udp443_forward.ps1` changes host NAT and installs `autostopvpn-udp443-forward.service`. Prefer `-DryRun` or `-WhatIf` first, confirm `47895/udp` remains active, and use `-Rollback` to remove only the UDP `443` forward and service. It must not restart or recreate `amnezia-awg2`.

`apply_telegram_keepalive_fix.ps1` changes live WireGuard peer keepalive and the live container config. Prefer `-DryRun` or `-WhatIf` first, use the shared SSH key resolver or `-Local` when running directly on the VPS, keep the generated `/root/autostopvpn-backups/awg0.conf.keepalive.bak.<timestamp>` path, and avoid running it during provider instability.

`apply_telegram_mtu_fix.ps1` changes the live VPN container MTU. Prefer `-DryRun` or `-WhatIf` first, use the shared SSH key resolver, and avoid running it during provider instability unless the MTU fix is the intended action.

`apply_telegram_mss_fallback.ps1` changes live container firewall rules and the container start script. Prefer `-DryRun -NoRestart` or `-WhatIf -NoRestart` first, keep the generated `/root/autostopvpn-backups/start.sh.mss.bak.<timestamp>` path for rollback, use it only after the mobile keepalive rollout is tested, and do not treat it as part of normal read-only diagnostics.

`apply_telegram_ipv6_relay_fix.ps1` changes host systemd and iptables rules for Telegram destinations on the current VPS. Prefer `-DryRun` or `-WhatIf` first, verify `Telegram IPv4 to IPv6 relay state` after apply, and use `-Rollback` to remove only the relay without touching Amnezia peers, MTU, MSS, keepalive, or UDP endpoints.

`repair_local_amnezia_mtu.ps1` changes only the local Windows `AmneziaVPN` interface MTU and the persistent `AmneziaWGTunnel$AmneziaVPN` service ImagePath. It requires an elevated PowerShell session for apply or rollback, writes a registry backup under `%LOCALAPPDATA%\AutostopVPN\secret-backups`, and must never change server peers, systemd units, routes, or iptables.
