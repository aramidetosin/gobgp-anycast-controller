# Design B: EVPN Multihoming (ESI)

Live `nv config show -o commands` snapshot of the whole two-DC fabric in the **EVPN
Multihoming** design (migrated 2026-08-15). See [../DESIGNS.md](../DESIGNS.md) for the
full comparison and the Cumulus VX caveats.

- `dc1/`, `dc2/` all Cumulus switches. Each access-leaf pair runs EVPN-MH: host bonds are
  Ethernet Segments (`evpn multihoming segment`, es-sys-mac = the old clag mac), uplink
  tracking on the spine ports, each leaf its own VTEP, no peerlink, no clagd. Both border
  pairs are independent VTEPs (no `nve vxlan mlag shared-address`); the DC leaves ECMP
  their default across both border VTEPs.
- `aggr/`, `firewall/`, `k8s/` unchanged from Design A (copied for a self-contained snapshot).

Every access-leaf Ethernet Segment verified `LR` (local + remote), zero MLAG remaining.
RFC1918 only, SNMP community redacted, no credentials.

Restore a switch by replaying its `nv set` lines, `nv config apply`, then **reboot** (VX
needs it to converge the ES bonds), then in vtysh `evpn mh redirect-off` + `write memory`.
