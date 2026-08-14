# ecloud GoBGP anycast controller

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
