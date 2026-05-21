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
- Keep `README.md`, `CODEX_PROJECT_MAP.md`, `LOCAL_INSTALL.md`, `AMNEZIA_VPN_MONITORING.md`, and `ACCESS_AND_DOCS.md` aligned when behavior changes.

## Safe Optimization Order

1. Run the baseline audit and `python -m unittest discover -s tests -v`.
2. Make one small change package at a time: docs, script safety, collector internals, shell internals, performance, or tests.
3. Preserve these public contracts: `collect/report/status/doctor`, dashboard JSON shape, `%LOCALAPPDATA%\AutostopVPN`, desktop shortcut flow, systemd unit names, and `127.0.0.1:18080/dashboard.json`.
4. Keep refresh-loop work light: geo and MTU probes must stay cached, and the shell should keep the repeated-snapshot fast path.
5. After local verification, sync to the GitHub `autostopVPN` branch first; server mirror updates under `/opt/autostopcrm` are a separate confirmed rollout step.

## High-Risk Helpers

`apply_telegram_mtu_fix.ps1` changes the live VPN container MTU. Prefer `-DryRun` or `-WhatIf` first, use the shared SSH key resolver, and avoid running it during provider instability unless the MTU fix is the intended action.

`apply_telegram_mss_fallback.ps1` changes live container firewall rules and the container start script. Prefer `-DryRun -NoRestart` or `-WhatIf -NoRestart` first, use it only after the mobile keepalive pilot, and do not treat it as part of normal read-only diagnostics.
