# ecloud GoBGP anycast controller

![GoBGP](https://img.shields.io/badge/engine-GoBGP-1971c2)
![Python 3](https://img.shields.io/badge/python-3-3776AB?logo=python&logoColor=white)
![BGP](https://img.shields.io/badge/BGP-EVPN%2FVXLAN-2f9e44)
![anycast](https://img.shields.io/badge/pattern-anycast-e8590c)
![steering](https://img.shields.io/badge/steering-health%20%2B%20latency-9c36b5)

Host-based anycast controller for the ecloud two-DC fabric. Two GoBGP nodes
(gobgp-1 active / gobgp-2 standby, AS 65400, iBGP to both br-agg switches)
re-originate a set of VIPs, each with next-hop = the chosen DC's regional VIP
(.202.1 DC1 / .202.2 DC2); the aggr resolves that recursively. The controller
never carries user traffic.

## Managed VIPs (brain.json `managed[]`)
- 192.168.202.0  global      policy failover, primary DC1
- 192.168.202.3  client-1    policy latency, primary DC1 (leave 1.5ms / return 1.0ms)
- 192.168.202.4  ext-client-2 policy static, DC2

## The brain (controller_brain.py)
Health-checks .202.1/.202.2 over HTTP, EWMA-smooths the rtt (rtt_alpha 0.35,
2-sample warmup), decides each VIP's DC from its policy + optional override,
and injects `gobgp global rib add <vip> nexthop <dc-vip> local-pref <lp>`
(500 active / 450 standby). Reconciles after a gobgpd restart. Writes
/run/gobgp-brain-status.json.

Policy modes: static (affinity) | failover (primary+backup) | latency
(EWMA-smoothed, primary-preferring, two-threshold hysteresis).

## Files
- controller_brain.py      -> /opt/gobgp-brain/controller_brain.py (identical both nodes)
- set_override.py          -> /opt/gobgp-brain/set_override.py
- nodes/<host>/brain.json  -> /etc/gobgp/brain.json   (role/local-pref differ per node)
- nodes/<host>/gobgpd.conf -> /etc/gobgp/gobgpd.conf  (router-id differs per node)
- nodes/<host>/systemd-units.txt (reference)
- scripts/ deploy + spike-test helpers

## Restore a node
Copy controller_brain.py + set_override.py to /opt/gobgp-brain/, the node's
brain.json + gobgpd.conf to /etc/gobgp/, then `systemctl enable --now gobgpd gobgp-brain`.

## Aggr (loop prevention, both br-agg switches)
prefix-list PL-ANYCAST-VIP {.202.0,.202.3,.202.4} -> route-map RM-DENY-ANYCAST-OUT
outbound on swp1/2/5/6.100 (tenant-k8s).

## Firewall (PA A/A)
DNAT 10.80.15.53->.202.3 (client-1), .54->.202.4 (ext-client-2); the external
addresses must also be in the internet-to-k8s security allow rule.

## fabric/ (two design snapshots: MLAG and EVPN-MH)
The whole two-DC fabric is captured live by hostname (mgmt IPs are DHCP and drift), in
**both** redundancy designs so the POC can show them side by side. See
[DESIGNS.md](DESIGNS.md) for the full comparison and the Cumulus VX caveats.

- `fabric-mlag/`    Design A: MLAG (clag + peerlink), shared anycast VTEP (the original build).
- `fabric-evpn-mh/` Design B: EVPN Multihoming (ESI) + independent-VTEP borders (migrated 2026-08-15).

Runbook: [TRACING.md](TRACING.md) shows the command per device to trace a MAC (L2, in a DC)
or a service/IP (L3, across both DCs), with a live worked example and each stage explained.

Each design dir has the same layout (only the leaf/border configs differ between them):

- `dc1/` - all 10 DC1 Cumulus switches (spine-1/2, leaf-k8s-master-1/2,
  leaf-k8s-worker-1/2, leaf-service-1/2, leaf-border-1/2), `nv config show -o commands`.
- `dc2/` - all 8 DC2 Cumulus switches (dc2-spine-1/2, dc2-k8s-leaf-1/2,
  dc2-svc-leaf-1/2, dc2-border-1/2).
- `aggr/` - the two backbone switches (br-agg-sw-1/2, AS 65400). The anycast loop
  filter (`PL-ANYCAST-VIP` -> `RM-DENY-ANYCAST-OUT` on swp1/2/5/6.100) lives here. Same in both.
- `firewall/fw-{pri,sec}.rules.set` - both Palo Altos, NAT + security rulebases
  (set format): `cli1-dnat`/`cli2-dnat` and the `internet-to-k8s` allow (with .53/.54).
  Writing to the PANs needs an operator (host safety gate); paste into `configure` + `commit`. Same in both.
- `k8s/perclient-vips.yaml` - the CiliumLoadBalancerIPPool + `dc-demo-cli1`/`cli2`
  LB services; `kubectl apply` to BOTH dc1 and dc2. Same in both.

Restore a MLAG switch: replay its `nv set` lines, then `nv config apply`.
Restore an EVPN-MH switch: replay, `nv config apply`, **reboot** (VX converges the ES
bonds only on boot), then vtysh `evpn mh redirect-off` + `write memory`.
Full PAN config (interfaces/zones/HA/credentials) and host archives stay in the
timestamped tarballs on eve-office:/root/backups/, not in this repo.
