# Autostop VPN Technical Report

## 1. What This System Is

This deployment is a self-hosted VPN based on AmneziaVPN with an AmneziaWG tunnel on the server. It is not a generic proxy and not a browser tunnel. The actual VPN data path is a UDP-based encrypted tunnel that carries the user's traffic from the client device to the VPS and then out to the internet.

The repository in this workspace contains the monitoring and desktop shell around that VPN. It does not replace the VPN data plane. The production VPN itself lives on the server in a Docker container.

## 2. High-Level Topology

```text
Phone / PC
  |
  | AmneziaVPN client
  | AmneziaWG tunnel over UDP
  v
Public server IP: 46.8.254.243
  |
  | Docker container: amnezia-awg2
  | WireGuard-style interface: awg0
  v
Internet / Telegram / websites
```

There are two different tunnels in this setup:

1. The VPN tunnel itself, from the device to the VPS.
2. A separate SSH tunnel used only by the desktop dashboard to read monitoring data from the server.

## 3. Protocol Stack

The VPN connection in this deployment is AmneziaWG, not plain WireGuard and not OpenVPN.

What that means:

1. The cryptographic core is WireGuard-compatible.
2. Transport is UDP.
3. AmneziaWG adds obfuscation / mimicry so the traffic is harder to identify by DPI systems.

Official Amnezia documentation describes AmneziaWG as a fork of WireGuard-Go that keeps the WireGuard cryptographic model while modifying packet appearance at the transport layer. WireGuard itself uses a `Noise_IK` handshake and sends all packets over UDP.

## 4. What Runs On The Server

The server-side roles are split like this:

1. `amnezia-awg2` Docker container
   - This is the live VPN container.
   - It holds the `awg0` interface.
   - It is the component that actually accepts client VPN packets.

2. `amnezia-traffic-collector.service`
   - Runs the telemetry collector.
   - Reads `wg show awg0 dump` from inside the container.
   - Computes traffic, activity, MTU, ping, bandwidth usage, and health data.

3. `amnezia-dashboard.service`
   - Serves the latest snapshot on `127.0.0.1:18080`.
   - This is localhost-only on the server.

The live config is inside the container filesystem, not on a bind mount. That is why the monitoring layer is kept separate from the VPN container itself.

## 5. What Runs On The Desktop

The Windows desktop side is the shell and launcher:

1. `start_autostopvpn.ps1`
   - Stable entrypoint from the desktop shortcut.

2. `open_amnezia_dashboard.ps1`
   - Resolves Python and launches the native shell app.

3. `amnezia_vpn_shell.py`
   - Opens the native dashboard window.
   - Starts an SSH tunnel to the server.
   - Reads the dashboard JSON through that tunnel.

The shell is not part of the VPN data path. It only displays status.

## 6. Ports And Endpoints

| Port / endpoint | Direction | Purpose |
| --- | --- | --- |
| `47895/udp` | Internet-facing on VPS | Main AmneziaWG VPN listener |
| `443/udp` | Internet-facing on VPS | Alternate mobile endpoint, DNAT to `47895/udp` without restarting the VPN container |
| `22/tcp` | Desktop to VPS | SSH access for the launcher and admin tasks |
| `18080/tcp` | Server localhost only | Dashboard HTTP service on `127.0.0.1:18080` |
| `18765/tcp` | Desktop localhost only | Local port exposed by the SSH tunnel to the shell app |

Important detail:

- `18080` is not public.
- `18765` is not public.
- The stable public VPN port is `47895/udp`.
- The alternate mobile profile endpoint is `46.8.254.243:443/udp`.

## 7. Data Flow Inside The Monitoring Layer

The monitoring flow works like this:

1. The collector runs every second.
2. It reads live WireGuard state from `docker exec amnezia-awg2 wg show awg0 dump`.
3. It measures ping, path MTU, container MTU, memory, disk, load, and uptime.
4. It writes `summary.json`, `totals.json`, day files, CSV/MD reports, and dashboard JSON.
5. The dashboard server serves `summary.json` over localhost on the server.
6. The desktop shell opens an SSH tunnel and reads that JSON through `127.0.0.1:18765`.

This is why the desktop app can show live status without exposing the dashboard to the internet.

## 8. Current Configurables

The current deployment metadata is stored in [`amnezia_server_info.json`](amnezia_server_info.json):

- public IP: `46.8.254.243`
- hostname: `vps26457.mnogoweb.in`
- domain: `crm.autostopcrm.ru`
- SSH user: `root`
- SSH port: `22`
- VPN container: `amnezia-awg2`
- interface: `awg0`
- configured WireGuard MTU: `1280`
- provider bandwidth limit: `1000 Mbps`

The collector also uses these operational settings:

- `AMNEZIA_CONTAINER=amnezia-awg2`
- `AMNEZIA_INTERFACE=awg0`
- `AMNEZIA_TRAFFIC_DIR=/var/lib/amnezia-traffic`
- `AMNEZIA_TRAFFIC_TZ=Asia/Krasnoyarsk`
- `AMNEZIA_PING_TARGET=1.1.1.1`
- `AMNEZIA_ACTIVE_WINDOW_SECONDS=180`
- `AMNEZIA_PING_COUNT=3`
- `AMNEZIA_MTU_PROBE=1`
- `AMNEZIA_MTU_TARGET=1.1.1.1`
- `AMNEZIA_MTU_PROBE_CACHE_SECONDS=3600`

## 9. Why Telegram Can Lag

Telegram problems over VPN are usually not caused by Telegram itself. The common causes are:

1. MTU mismatch.
2. Fragmentation or dropped packets on the UDP path.
3. Bad path MTU on the route to the server or from the server out to Telegram.
4. Lossy mobile network or ISP path combined with VPN overhead.

In this deployment, the project already probes:

- live `awg0` MTU inside the container
- path MTU to the configured probe target
- bandwidth load and headroom

The current working MTU target is `1280`. If Telegram voice notes, stickers, or media stall, the first thing to inspect is whether the effective tunnel MTU is too high for the path.

## 10. Practical Interpretation

If someone asks “what is happening when I click Connect in Amnezia?”, the short answer is:

1. The client builds an AmneziaWG tunnel to the VPS over UDP.
2. The VPS decrypts the packets inside `amnezia-awg2`.
3. `awg0` routes the traffic to the internet.
4. Replies come back through the same tunnel.
5. The desktop app only monitors this process and does not carry the VPN traffic itself.

## 11. Relevant Local Files

- [`README.md`](README.md)
- [`AMNEZIA_VPN_MONITORING.md`](AMNEZIA_VPN_MONITORING.md)
- [`amnezia_server_info.json`](amnezia_server_info.json)
- [`amnezia_traffic_collector.py`](amnezia_traffic_collector.py)
- [`amnezia_dashboard_server.py`](amnezia_dashboard_server.py)
- [`amnezia_vpn_shell.py`](amnezia_vpn_shell.py)
- [`amnezia-traffic-collector.service`](amnezia-traffic-collector.service)
- [`amnezia-traffic-collector.timer`](amnezia-traffic-collector.timer)
- [`amnezia-dashboard.service`](amnezia-dashboard.service)
- [`check_autostopvpn_network.ps1`](check_autostopvpn_network.ps1)
- [`scheduled_recovery_checks.ps1`](scheduled_recovery_checks.ps1)
- [`repair_local_amnezia_mtu.ps1`](repair_local_amnezia_mtu.ps1)

## 12. 2026-06-10 Stability Recovery Notes

The June 2026 slowdown was reproduced on the Windows peer `10.8.1.36/32`.
The server-side VPN baseline was healthy: `amnezia-awg2` stayed up, all peers had `PersistentKeepalive=25`, `awg0` was `MTU=1280`, and generic TCP MSS clamp `1240` was active.

The local Windows tunnel was the outlier: the active AmneziaVPN interface and service ImagePath used `MTU=1376`.
Changing the active IPv4/IPv6 interface MTU to `1280` and persisting `MTU = 1280` in the `AmneziaWGTunnel$AmneziaVPN` service ImagePath removed the packet-loss symptom in verification samples:

- `1.1.1.1`: `100/100` replies, `0%` loss
- `api.telegram.org`: `100/100` replies, `0%` loss
- Telegram HTTPS: HTTP `200`
- server read-only monitor: gateway, `1.1.1.1`, `8.8.8.8`, and Telegram API all `0%` loss in the post-fix sample

After the first recovery samples, the only recurring packet-loss signal was intermittent ICMP loss on the Google route (`8.8.8.8`) while Cloudflare (`1.1.1.1`) and Telegram stayed healthy.
The server resolver was static Google DNS (`8.8.4.4`, `8.8.8.8`), and the VPS RTT to Google was roughly `87-89 ms` compared with about `2 ms` to Cloudflare.
On 2026-06-10 the host `/etc/resolv.conf` and the running `amnezia-awg2` container resolver were moved to Cloudflare DNS:

- `1.1.1.1`
- `1.0.0.1`

Backups were written on the server under `/root/autostopvpn-backups/`:

- `resolv.conf.googledns.bak.20260609-193623`
- `amnezia-awg2-resolv.conf.googledns.bak.20260609-193623`

The scheduled recovery monitor now treats Cloudflare and Telegram packet loss as critical. `8.8.8.8` remains in the log as a non-critical Google route comparison.
The intended recurring Task Scheduler profile uses `-PingCount 10`, `-SampleSeconds 5`, and `-LocalDownloadBytes 5242880` so that the 15-minute health check finishes quickly. Longer manual incident checks can still use the README command with `-PingCount 30` and a 10 MB download probe. On 2026-06-19, this workstation did not show registered Windows Task Scheduler jobs matching `Autostop` or `VPN`, so recovery checks should be run manually until the tasks are recreated.
The intended daily deep-check workflow runs at 08:00 Asia/Krasnoyarsk. A Codex thread wake-up named `AutostopVPN daily deep health check` performs the operator review and remediation loop. The matching local Windows Task Scheduler job should run the heavy probe profile: `-PingCount 60`, `-SampleSeconds 30`, `-DownloadBytes 52428800`, and `-LocalDownloadBytes 52428800`.

The latest scheduled run on 2026-06-10 at 03:17 local time completed with `LastTaskResult=0`, `Health warnings: 0`, and `Health failures: 0`. In that run:

- local `1.1.1.1`: `10/10`, `0%` loss
- local `1.0.0.1`: `10/10`, `0%` loss
- local `8.8.8.8`: `10/10`, `0%` loss
- local Telegram ping: `10/10`, `0%` loss
- local Cloudflare download: about `30.8 Mbps`
- server `1.1.1.1`: `10/10`, `0%` loss
- server `1.0.0.1`: `10/10`, `0%` loss
- server `8.8.8.8`: `10/10`, `0%` loss
- server Telegram API: `telegram_api_https_ok=true`

## 13. 2026-06-11 Audit Update

The server was rechecked read-only on 2026-06-11 after a recent reboot. The target runtime remains stable:

- `amnezia-awg2` running, public UDP `47895` published
- UDP `443` DNAT active and forwarding to the existing container listener
- monitoring services inactive until the desktop shell opens
- `57` peers configured
- server-side `PersistentKeepalive=25` on all peers
- live/config `awg0 MTU=1280`
- generic `awg0` TCP MSS clamp `1240`
- Telegram IPv4 to IPv6 relay service active
- Cloudflare, Google comparison, Telegram API, and OpenAI API checks showed `0%` packet loss or expected HTTPS status in the sampled checks
- `api.openai.com/v1/models` returned the expected unauthenticated HTTP `401`; `chatgpt.com` reached Cloudflare with HTTP `403`

The server still shows provider-gateway jitter without packet loss. In the long sample, gateway RTT reached about `252 ms` while packet loss stayed at `0%`. This is evidence to avoid server restarts or repeated MTU/MSS changes when the data path is otherwise healthy.

The local Windows client was the remaining outlier. On 2026-06-11 the active `AmneziaVPN` IPv4/IPv6 interface and persistent tunnel service were again at `MTU=1376`, while the target profile is `1280`. The unelevated Codex process first created a registry backup and proved the issue; after UAC elevation, `repair_local_amnezia_mtu.ps1` applied the active IPv4/IPv6 MTU and persistent service ImagePath change. The post-repair scheduled recovery run completed with `Health warnings: 0` and `Health failures: 0`.

Do not remove the Telegram relay or UDP `443` endpoint yet. The public Amnezia incident update says the infrastructure fix found on 2026-06-08 was still being rolled out over several days, and this server's relay counters are still increasing. A valid A/B removal test needs an elevated/local MTU repair first, a fresh passing baseline, a documented rollback, and a low-traffic maintenance window.

## 14. 2026-06-19 Current Update

The server was rechecked read-only on 2026-06-19. The current runtime differs from the older 2026-06-11 inventory:

- `67` peers configured
- `67` peers with server-side `PersistentKeepalive=25`
- `28` active peers in the latest 180 second collector sample
- `33` active peers within 600 seconds in the read-only monitor sample
- `15` never-handshaked peers in the latest read-only monitor sample
- live/config `awg0 MTU=1280`
- generic TCP MSS clamp `1240`
- UDP `443` forward active
- Telegram relay active
- OpenAI and Telegram HTTPS checks pass
- critical ping samples show `0%` packet loss
- provider gateway jitter is still visible without packet loss; one short sample reached about `101 ms` max RTT to the provider gateway while internet pings stayed at `0%` loss
- local Windows `AmneziaVPN` IPv4/IPv6 interfaces and persistent service ImagePath are currently `MTU=1280`
- local recovery check finished with `Health failures: 0` and one non-critical warning: local VPN download `19.13 Mbps`, below the `20 Mbps` warning threshold and above the `5 Mbps` failure threshold; a server-side Cloudflare sample reached about `42.7 Mbps`, so this is a local/client-route warning rather than a VPS channel failure
- no Windows Task Scheduler jobs matching `Autostop` or `VPN` were registered on this workstation; run recovery checks manually until recurring jobs are recreated
- owner/full-access recovery on 2026-06-19 added peer `10.8.1.69/32`; `apply_telegram_keepalive_fix.ps1` then normalized all peers to `PersistentKeepalive=25` and wrote backup `/root/autostopvpn-backups/awg0.conf.keepalive.bak.20260619-173830`
- a later duplicate-card/local recovery check found a new peer `10.8.1.70/32` without keepalive; the keepalive helper was rerun and wrote backup `/root/autostopvpn-backups/awg0.conf.keepalive.bak.20260619-175622`, after which `keepalive_off=0`

## 15. Client Rollout Impact

The live server state confirms that the server-side changes are already active for all peers that connect:

- total peers: `67`
- active within 180 seconds in the latest sample: `28`
- active within 600 seconds in the latest read-only monitor sample: `33`
- never-handshaked peers in the latest read-only monitor sample: `15`
- server-side `PersistentKeepalive=25`: `67`
- server-side `PersistentKeepalive=0`: `0`
- live/config `awg0 MTU`: `1280`
- generic TCP MSS clamp rules: `2`
- UDP `443` forward: active

The 2026-06-19 collector status sample showed channel flow around `797.79 KiB/s`, utilization around `0.65%` of the configured 1 Gbps limit, and collector warnings `0`. A separate 5 second read-only traffic delta saw about `1.37 MiB/s` and `47` peers with traffic movement.

These server-side settings protect the shared VPN path and do not require a new key or server restart. Existing clients on `46.8.254.243:47895` continue to work because `47895/udp` remains published.

The server cannot prove or rewrite local client profile fields. A phone or desktop keeps its existing `Endpoint`, local `MTU`, `DNS`, and client-side `PersistentKeepalive` until the profile is edited or re-imported. For lowest risk, update client profiles only where needed first:

1. users who reported Telegram/media slowness
2. mobile users
3. users on restrictive Wi-Fi/LTE networks
4. remaining active desktops during normal maintenance
5. stale/never-handshaked peers when the user returns

The target client profile delta is:

```ini
Endpoint = 46.8.254.243:443
MTU = 1280
PersistentKeepalive = 25
```

Keep existing keys and `AllowedIPs`.

The secret-bearing service ImagePath backups are intentionally outside the repository under `%LOCALAPPDATA%\AutostopVPN\secret-backups`.
Recurring short recovery checks are logged under `%LOCALAPPDATA%\AutostopVPN\logs`.

## 16. Official References

- [AmneziaWG docs](https://docs.amnezia.org/ru/documentation/amnezia-wg/)
- [Amnezia May-June 2026 incident summary](https://amnezia.org/ru/blog/amnezia-vpn-may-june-2026-incident-preliminary-summary)
- [How Amnezia works](https://docs.amnezia.org/documentation/how-amnezia-works/)
- [WireGuard protocol](https://www.wireguard.com/protocol/)
- [WireGuard quick start](https://www.wireguard.com/quickstart/)
