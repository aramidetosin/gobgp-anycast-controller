# Tracing a MAC end-to-end on the Cumulus EVPN-VXLAN fabric

The same structured, layer-by-layer trace you'd run on a Nexus fabric, in the order you
actually troubleshoot: **underlay first, then the EVPN control plane, then the VXLAN data
plane, then L2 forwarding, then L3, then the multihoming, then the host workflow, then a
fabric health snapshot.** This lab is Cumulus Linux (FRR + Linux), so the commands are
`vtysh` and Linux `bridge`/`ip`, not NX-OS `show nve`/`show l2route`. Every output below
was captured live on 2026-08-15, with the real device prompt so you can see where it ran.

**Traced object:** MAC `50:00:00:0e:00:01` = host `10.167.20.11` (DC1 k8s-worker-1 data0),
dual-homed to the `leaf-k8s-worker` pair by EVPN Multihoming. L2VNI **10120** (VLAN 120),
L3VNI **50001**, VTEPs `10.0.0.13` (leaf-1) / `10.0.0.14` (leaf-2), fabric is **eBGP
underlay + eBGP L2VPN-EVPN** (leaf AS 65112, spines AS 65100).

## NX-OS to Cumulus command map
| Cisco NX-OS | Cumulus (FRR / Linux) |
|---|---|
| `show ip bgp summary` | `vtysh -c "show bgp ipv4 unicast summary"` |
| `show ip route <vtep>` / `show ip cef` | `vtysh -c "show ip route <vtep>"` ; `ip route get <ip>` |
| `show bgp l2vpn evpn summary` | `vtysh -c "show bgp l2vpn evpn summary"` |
| `show bgp l2vpn evpn route-type 2` | `vtysh -c "show bgp l2vpn evpn route type macip"` |
| `show bgp l2vpn evpn route-type 3` | `vtysh -c "show bgp l2vpn evpn route type multicast"` |
| `show bgp l2vpn evpn route-type 4` (ES) | `vtysh -c "show bgp l2vpn evpn route type es"` |
| `show bgp l2vpn evpn route-type 5` | `vtysh -c "show bgp l2vpn evpn route type prefix"` |
| `show nve interface nve1 detail` | `ip -d link show vxlan48` |
| `show nve peers` | `vtysh -c "show evpn vni <vni>"` (Remote VTEPs) |
| `show nve vni` | `vtysh -c "show evpn vni"` |
| `show mac address-table` | `bridge fdb show` ; `net show bridge macs` |
| `show l2route evpn mac all` | `vtysh -c "show evpn mac vni all"` |
| `show l2route evpn mac-ip all` | `vtysh -c "show evpn arp-cache vni all"` |
| `show ip arp suppression-cache detail` | `vtysh -c "show evpn arp-cache vni <vni>"` |
| `show ip arp vrf <vrf>` | `ip -4 neigh show vrf <vrf>` |
| `show ip route vrf <vrf> <ip>` | `vtysh -c "show ip route vrf <vrf> <ip>"` |
| `show vpc` / `show port-channel summary` | `vtysh -c "show evpn es"` ; `cat /proc/net/bonding/<bond>` |
| `show consistency-checker` (N9K ASIC) | n/a on VX (no ASIC); real HW: `cl-support`, `nv show` |

Note: dual-homing here is **EVPN Multihoming (ESI)**, not vPC, so the "vPC" checks become
the Ethernet-Segment checks in section 6.

---

## 1. Underlay (eBGP IPv4 unicast): VTEP reachability

The underlay's only job is loopback (VTEP) reachability between leaves, via the spines.
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp ipv4 unicast summary"
Neighbor        V   AS   MsgRcvd  MsgSent  Up/Down  State/PfxRcd
spine-1(swp1)   4  65100   4886     4885   03:59:04     9
spine-2(swp2)   4  65100   4887     4886   03:59:08     9
Total number of neighbors 2
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route 10.0.0.14"     # the peer VTEP
Routing entry for 10.0.0.14/32
  Known via "bgp", distance 20, metric 0, best
  * fe80::5200:ff:fe01:3, via swp1, weight 1
  * fe80::5200:ff:fe02:3, via swp2, weight 1
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Checks:** a session per spine, Established, prefixes received > 0. The peer VTEP
`10.0.0.14` is reachable via **both** spines (ECMP) — if you see one path, check
`maximum-paths` and the spine underlay. MTU on fabric links must be jumbo (9216).
Live test: `ping 10.0.0.14 -I 10.0.0.13 -M do -s 9000` (df-bit, jumbo).

## 2. Overlay control plane (eBGP L2VPN EVPN)
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn summary"
spine-1(swp1)   4  65100   ... Established   128
spine-2(swp2)   4  65100   ... Established   128
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type macip" | grep -A2 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01]:[32]:[10.167.20.11] RD 10.0.0.13:2
      ESI:03:44:38:39:be:ef:12:00:00:01  RT:65000:10120 RT:65112:50001 Rmac:50:00:00:05:00:11
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.14:2   (leaf-k8s-worker-2)
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type es" | grep -A1 be:ef:12
*> [4]:[03:44:38:39:be:ef:12:00:00:01]:[32]:[10.0.0.13] RD 10.0.0.13:3
      10.0.0.13 (leaf-k8s-worker-1)  ET:8 ES-Import-Rt:44:38:39:be:ef:12 DF:(alg 2, pref 32767)
cumulus@leaf-k8s-worker-1:mgmt:~$
```
- **Type-2 (MAC/IP)** for the host, advertised from **both** ES VTEPs, each stamped with the
  ESI, the L2VNI RT (`65000:10120`), the L3VNI RT (`65112:50001`), and the router-mac.
- **Type-4 (Ethernet Segment)** advertises the ESI itself and drives DF election (this is the
  EVPN-MH equivalent of vPC; see section 6).
- Type-3 (IMET) sets up BUM/ingress-replication per L2VNI; Type-5 carries IP prefixes.

**The eBGP-over-eBGP check (the classic failure):** on the spine, the Type-2 next-hop must
stay the **originating leaf's VTEP**, not the spine, or remote VTEP resolution breaks. Verify:
```
cumulus@spine-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type macip" | grep -A2 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.13:2
      10.0.0.13 (leaf-k8s-worker-1)          0 65112 i
cumulus@spine-1:mgmt:~$
```
Next-hop `10.0.0.13` (the leaf), AS-path `65112` (the leaf's AS): the spine keeps the leaf as
next-hop and retains the route-targets. Correct EVPN transit.

## 3. VXLAN data plane (the NVE / VNI equivalent)
```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -d link show vxlan48
    vxlan external ... local 10.0.0.13 ... dstport 4789 ... neigh_suppress on
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn vni 10120"
VNI: 10120   Type: L2   Vlan: 120   Tenant VRF: tenant-k8s   SVI interface: vlan120
 Local VTEP IP: 10.0.0.13
 Remote VTEPs for this VNI:
  10.0.0.14 flood: HER
 Number of MACs (local and remote) known for this VNI: 5
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Checks:** `Remote VTEPs ... 10.0.0.14 flood: HER` is the "nve peer" for this L2VNI (one entry
per remote leaf carrying the VNI; `HER` = head-end / ingress replication). A missing remote
VTEP means no Type-3 received from it (back to section 2). `show evpn vni` lists every L2VNI
and L3VNI and their state.

## 4. L2 forwarding and host learning
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(23) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Neighbors:  10.167.20.11 Active
cumulus@leaf-k8s-worker-1:mgmt:~$
```
`show evpn mac vni <vni>` is the bridge between BGP and the MAC table (the `l2route`
equivalent): a MAC shows **local** on its attached leaf and **remote** (with the origin VTEP)
elsewhere. Here it is `local` on `bond1`, tied to the ESI, and `peer-active` (synced with the
ES peer over EVPN, not a peerlink). The kernel data-plane view is `bridge fdb show` /
`net show bridge macs`; on this VX lab a quiescent ESI host may sit only in the EVPN table
above until it forwards traffic, so treat `show evpn mac` as authoritative.

## 5. L3 overlay (anycast gateway, symmetric IRB, ARP suppression)
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn arp-cache vni 10120" | grep 10.167.20.11
10.167.20.11         local  P  active  50:00:00:0e:00:01                0/0
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 10.167.20.11"
Routing entry for 10.167.20.0/24
  Known via "connected", ... vrf tenant-k8s   (SVI vlan120, anycast gateway)
cumulus@leaf-k8s-worker-1:mgmt:~$
```
`show evpn arp-cache vni <vni>` is the ARP/ND suppression cache (populated from Type-2), the
equivalent of `show ip arp suppression-cache`: `10.167.20.11 -> 50:00:00:0e:00:01`, local,
`P` = peer-synced. Because the host is **local**, its route in the tenant VRF is the
**connected** subnet on the anycast-gateway SVI `vlan120`; a **remote** host would appear as
a `/32` via BGP with the remote VTEP as next-hop and the L3VNI as encap.

## 6. EVPN Multihoming (the Ethernet Segment) - replaces the vPC checks
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn es"
ESI                            Type ES-IF   VTEPs
03:44:38:39:be:ef:12:00:00:01  LR   bond1   10.0.0.14
03:44:38:39:be:ef:12:00:00:02  LR   bond2   10.0.0.14
03:44:38:39:be:ef:12:00:00:03  LR   bond3   10.0.0.14
cumulus@leaf-k8s-worker-1:mgmt:~$
```
`show evpn es` is the multihoming health, the EVPN-MH answer to `show vpc`. `Type LR` =
Local + Remote: this leaf owns the segment locally and sees the peer VTEP `10.0.0.14` for the
same ESI. The bond's LACP presents the es-sys-mac `44:38:39:be:ef:12` so the host bonds to
both leaves as one; DF election (from the Type-4 route in section 2) picks who forwards BUM.

## 7. Tracing the MAC end-to-end (the workflow)
1. **Leaf it is local to** - `show evpn mac vni 10120 mac 50:00:00:0e:00:01` -> local on
   `bond1`, ESI, `peer-active`, host `10.167.20.11` (section 4).
2. **Its IP** - `show evpn arp-cache vni 10120 | grep 10.167.20.11` -> `50:00:00:0e:00:01` (section 5).
3. **Its advertisement** - `show bgp l2vpn evpn route type macip | grep 0e:00:01` -> Type-2 from
   **both** VTEPs `10.0.0.13` and `10.0.0.14`, each with the ESI (section 2).
4. **The ES peer** - on `leaf-k8s-worker-2`, `show evpn mac vni 10120 mac 50:00:00:0e:00:01` ->
   local on **its** `bond1`, same ESI: the dual-homing.
5. **Reach the peer VTEP** - `show evpn vni 10120` (Remote VTEP `10.0.0.14`) and
   `show ip route 10.0.0.14` (ECMP via both spines, section 1).
6. **The segment** - `show evpn es` -> `LR`, VTEP `10.0.0.14` (section 6).
7. **Live** - `ping 10.167.20.11 -I vlan120` from a leaf; `ip -s link show vxlan48` for counters.

**Scope:** L2VNI 10120 lives only on the `leaf-k8s-worker` pair, so this MAC is local on both
ES members and never crosses to DC2 (the backbone is L3 routed, not L2 stretched). To follow
the host across DCs, trace its **IP as a service** (see `TRACING.md`).

## 8. Fabric-wide health snapshot
```
sudo vtysh -c "show bgp l2vpn evpn summary"      # every leaf: session per spine, Established
sudo vtysh -c "show evpn vni"                    # every L2VNI/L3VNI Up
sudo vtysh -c "show evpn es"                     # every Ethernet Segment LR (multihoming health)
net show interface                               # link/error counters
sudo vtysh -c "show bgp l2vpn evpn route type es"  # DF election consistent across the pair
```

## The three failure classes to watch (eBGP-over-eBGP)
1. **Spine rewriting next-hop** - remote VTEPs would point at the spine, not the leaf. Verified
   correct in section 2 (next-hop stayed `10.0.0.13`).
2. **Spine dropping route-targets** - the L2VNI/L3VNI RTs must survive transit (section 2).
3. **Underlay ECMP asymmetry** - a hidden single-spine dependency. Section 1 shows the VTEP
   reachable via **both** spines.
