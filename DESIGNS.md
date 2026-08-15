# Two fabric designs: MLAG vs EVPN Multihoming

This repo snapshots the ecloud two-DC fabric in **both** redundancy designs so the POC
can show them side by side. Same topology, same tenants, same anycast controller and
firewall. Only the leaf and border redundancy mechanism changes.

- `fabric-mlag/`    Design A: MLAG (clag + peerlink), shared anycast VTEP.
- `fabric-evpn-mh/` Design B: EVPN Multihoming (ESI), independent VTEPs at the border.

Everything else (`nodes/`, `controller_brain.py`, `fabric-*/aggr`, `fabric-*/firewall`,
`fabric-*/k8s`) is identical between the two: the spines, aggr, PANs, GoBGP controller
and k8s LB services never changed.

## Design A: MLAG (clag + peerlink)

Each leaf pair is an MLAG domain. A physical **peerlink** carries clagd sync plus a
backup path, both leaves present a **shared anycast VTEP**, and dual-homed hosts bond to
both leaves via **clag bonds** (`bond mlag id N`). Borders share a VTEP the same way.
Loop-freedom and redundancy come from clagd.

Key per-leaf config: `mlag enable on`, `mlag mac-address`, `mlag peer-ip linklocal`,
`interface peerlink type peerlink` + `peerlink.4094`, `interface bondN bond mlag id N`,
`nve vxlan mlag shared-address <anycast-vtep>`.

## Design B: EVPN Multihoming (ESI)

No peerlink, no clagd. Each dual-homed host bond becomes an **Ethernet Segment** (ESI),
advertised by both leaves via EVPN Type-4 ES routes. DF election and split-horizon
filtering replace clag. Each leaf uses **its own VTEP**. Uplink tracking pulls a leaf's
Ethernet Segments when it loses all spine uplinks, so hosts single-home to the peer.

Key per-leaf config: `evpn multihoming enable on`, `nve vxlan arp-nd-suppress on`, per
bond `evpn multihoming segment local-id N` + `segment mac-address <es-sys-mac>`, per
spine uplink `evpn multihoming uplink on`.

The **es-sys-mac reuses the old clag `mlag mac-address`**, so the LACP system-id the
hosts see never changes. The cutover is host-transparent: the host bond never breaks.

**Borders** have no dual-homed hosts (they route north over per-VRF subinterfaces), so
there is nothing to put in an Ethernet Segment. Their MLAG was only a shared-VTEP
construct. In Design B each border is an **independent VTEP** and the DC leaves ECMP
their default route across both border VTEPs.

### Cumulus VX caveats (this is a VX lab, not Spectrum ASIC)

- After `nv config apply` the ES host bonds sit carrier-down until a **reboot**, and
  LACP then takes 2 to 3 minutes to converge. Real hardware does not need this.
- `evpn mh redirect-off` (vtysh) is required on VX and must be re-applied plus
  `write memory` after every reboot. VX has no ASIC fast-failover redirect.

## Validated end to end (2026-08-15)

- All 5 access-leaf pairs on ESI, every Ethernet Segment shows `LR` (local + remote),
  zero MLAG left anywhere.
- Both border pairs on independent VTEPs, DC leaves ECMP their default across both.
- Demo steering intact: global and client-1 to DC1, ext-client-2 to DC2.
- Redundancy proven: a worker leaf was failed (uplinks pulled, uplink-tracking pulled
  its Ethernet Segments) and the DC1 demo held 10/10 with hosts single-homed to the
  surviving leaf.
- Anycast failover proven live: global and client-1 rode DC2 during the worker and
  border reboots, then returned to DC1 on their own.
