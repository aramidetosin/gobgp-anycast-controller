# Design A: MLAG (clag + peerlink)

Live `nv config show -o commands` snapshot of the whole two-DC fabric in the **MLAG**
design (the original build). See [../DESIGNS.md](../DESIGNS.md) for the full comparison.

- `dc1/`, `dc2/` all Cumulus switches (spines, leaves, borders). Leaf and border pairs
  run clag with a peerlink and a shared anycast VTEP (`nve vxlan mlag shared-address`).
- `aggr/` the two backbone switches (br-agg-sw-1/2). Unchanged between designs.
- `firewall/` both PANs (NAT + security rulebases, set format). Unchanged between designs.
- `k8s/` CiliumLoadBalancerIPPool + per-client LB services. Unchanged between designs.

RFC1918 only, SNMP community redacted, no credentials. Restore a switch by replaying its
`nv set` lines then `nv config apply`.
