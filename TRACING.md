# Tracing a MAC or a service across the VXLAN/EVPN fabric

How to follow a **MAC** (Layer 2, stays inside one DC) or a **service / IP** (Layer 3,
crosses both DCs) through the ecloud fabric, device by device, with the command to run
at each stage and what its output means. Every command and output below was captured
live on the running fabric on 2026-08-15. Nothing here is invented.

## Two forwarding models (why a MAC and a service trace differently)

- **A MAC never leaves its DC.** Same-subnet hosts share an **L2VNI** (a VLAN mapped to
  a VXLAN VNI). The leaf a host is attached to advertises its MAC as an **EVPN Type-2
  (MAC/IP) route**; other leaves in the same L2VNI import it as remote. The br-agg
  backbone is L3 routed, not L2 stretched, so a MAC is contained to the leaves that
  carry its VLAN.
- **A service / IP crosses DCs over L3.** Inside a DC, reaching another subnet rides the
  **L3VNI** (symmetric IRB, EVPN **Type-5** IP-prefix routes). Between DCs it is plain
  routed over the br-agg backbone (VRF-per-tenant, no VXLAN). So a service trace walks:
  leaf -> spine -> border -> backbone -> border -> spine -> leaf -> host.

Both examples use tenant VRF `tenant-k8s`.
DC1: L2VNI **10120** (VLAN 120), L3VNI **50001**, VTEPs `10.0.0.x`.
DC2: L2VNI **10210** (VLAN 210), L3VNI **50101**, VTEPs `10.2.0.x`.

## Command per device

### Trace a MAC (inside a DC)
| Stage / device | Command |
|---|---|
| Leaf where the MAC is local | `sudo vtysh -c "show evpn mac vni <l2vni> mac <MAC>"` |
| " (kernel FDB + port) | `bridge fdb show \| grep <MAC>` |
| " (the Type-2 it originates) | `sudo vtysh -c "show bgp l2vpn evpn route type macip" \| grep -A3 <MAC>` |
| Any other leaf in that L2VNI | `sudo vtysh -c "show evpn mac vni <l2vni> mac <MAC>"` (shows `remote` + origin VTEP) |
| Spine (EVPN route-reflector) | `sudo vtysh -c "show bgp l2vpn evpn route type macip" \| grep <MAC>` and `show ip route <origin-VTEP>` |

### Trace a service / IP (across DCs)
| Stage / device | Command |
|---|---|
| Any L3 hop (leaf / border / backbone) | `sudo vtysh -c "show ip route vrf <tenant> <SERVICE-IP>"` |
| EVPN Type-5 (IP-prefix) detail | `sudo vtysh -c "show bgp l2vpn evpn route type prefix" \| grep <SERVICE-IP>` |
| L3VNI router-mac / next-hop VTEP | `sudo vtysh -c "show evpn rmac vni <l3vni>"` |
| Underlay reachability to a VTEP | `sudo vtysh -c "show ip route <VTEP-IP>"` |

> On the **br-agg backbone** switches, login is key-only and `sudo` still needs the
> password: run `echo <pw> | sudo -S vtysh -c "..."`, or FRR output comes back empty.

---

## Worked example A: MAC `50:00:00:0e:00:01` (DC1 k8s worker, L2VNI 10120)

This MAC is the `data0` NIC of DC1 **k8s-worker-1** (host `10.167.20.11`). It is
dual-homed to the worker-leaf pair by **EVPN Multihoming (ESI)**, so both leaves own
it. Watch the ESI thread through every stage.

### Stage 1 - `leaf-k8s-worker-1` (VTEP 10.0.0.13): the MAC is local here
```
$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(23) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Local Seq: 0 Remote Seq: 0
 Uptime: 00:47:07
 Neighbors:
    10.167.20.11 Active
    fe80::5200:ff:fe0e:1 Active
```
**What this stage tells you:** the MAC is *local* on `bond1`, VLAN 120. It belongs to
Ethernet Segment `03:44:38:39:be:ef:12:00:00:01` (the dual-homed bond), it is
`peer-active` (the leaf has synced it with its ES peer over EVPN, not a peerlink), and
its host IP is `10.167.20.11`. This is the leaf that will originate the Type-2 route.

### Stage 2 - `leaf-k8s-worker-2` (VTEP 10.0.0.14): also local (the multihoming)
```
$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(22) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Neighbors:
    10.167.20.11 Active
```
**What this stage tells you:** the *same* MAC is local on the peer leaf's own `bond1`,
under the *same* ESI. That is EVPN Multihoming: there is no MLAG and no peerlink; both
leaves independently advertise the segment, and the ESI is how the rest of the fabric
knows the two advertisements are the same host.

### Stage 3 - the EVPN Type-2 route that carries it (control plane)
```
$ sudo vtysh -c "show bgp l2vpn evpn route type macip" | grep -A3 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.13:2
                    10.0.0.13 (leaf-k8s-worker-1)   ESI:03:44:38:39:be:ef:12:00:00:01
                    ET:8 RT:65000:10120
*> [2]:[0]:[48]:[50:00:00:0e:00:01]:[32]:[10.167.20.11] RD 10.0.0.13:2
                    10.0.0.13 (leaf-k8s-worker-1)    ESI:03:44:38:39:be:ef:12:00:00:01
                    ET:8 RT:65000:10120 RT:65112:50001 Rmac:50:00:00:05:00:11
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.14:2
                    10.0.0.14 (leaf-k8s-worker-2)    ...
```
**What this stage tells you:** two flavours of the Type-2 route.
- `[2]...[50:00:00:0e:00:01]` is the **MAC-only** route (L2 bridging), tagged
  `RT:65000:10120` (the L2VNI 10120 route-target).
- `[2]...[50:00:00:0e:00:01]:[32]:[10.167.20.11]` is the **MAC+IP** route. It adds the
  host IP and carries the **L3VNI** route-target `RT:65112:50001` plus the router-mac
  `Rmac:50:00:00:05:00:11` used for symmetric IRB (routed reach to the host).
- Both are advertised from **RD 10.0.0.13:2** *and* **10.0.0.14:2** (both ES VTEPs),
  each stamped with the ESI. A remote leaf importing this sees one host reachable via
  two VTEPs and ECMPs to both. That is the multihoming, expressed in BGP.

### Stage 4 - `spine-1` (EVPN route-reflector + underlay)
```
$ sudo vtysh -c "show ip route 10.0.0.13"     # underlay reach to the origin VTEP
  * fe80::5200:ff:fe05:1, via swp3
```
**What this stage tells you:** the spine has no VTEP of its own. It reflects the Type-2
routes between leaves (control plane) and, in the data plane, simply routes the VXLAN
packets by their outer destination (the VTEP IP `10.0.0.13`) out `swp3`. Pure underlay.

> **Scope note:** L2VNI 10120 exists only on the worker-leaf pair, so this MAC is not
> learned anywhere else in DC1 and never crosses to DC2. To follow the host across DCs
> you trace its *IP as a service* (below), not its MAC.

---

## Worked example B: service `192.168.202.2` (DC2 dc-demo VIP), DC1 -> DC2

This is the DC2 regional LoadBalancer VIP, advertised into the fabric by the DC2
dc-demo pods. Traced from a DC1 leaf, it climbs out of DC1's overlay, crosses the
routed backbone, and drops into DC2's overlay. `show ip route vrf tenant-k8s
192.168.202.2` at each hop shows the next device.

### Stage 1 - DC1 `leaf-k8s-worker-1`: into the L3VNI toward the DC1 borders
```
$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * 10.0.0.17, via vlan3001_l3 onlink, weight 1
  * 10.0.0.18, via vlan3001_l3 onlink, weight 1
```
**Stage explained:** the service lives in DC2, so this leaf cannot reach it locally. It
resolves it through the **L3VNI** SVI `vlan3001_l3` (symmetric IRB) toward the two DC1
**border** VTEPs `10.0.0.17` / `10.0.0.18`, ECMP. The leaf VXLAN-encapsulates the
packet with a border VTEP as the outer destination.

### Stage 2 - DC1 `spine-1`: underlay carry to the border VTEP
```
$ sudo vtysh -c "show ip route 10.0.0.17"
  * fe80::5200:ff:fe09:1, via swp7
```
**Stage explained:** the spine routes the encapsulated packet by its outer VTEP
(`10.0.0.17`) out `swp7` to the border. It does not look inside the tunnel.

### Stage 3 - DC1 `leaf-border-1`: overlay meets the routed backbone
```
$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
  * fe80::5200:ff:fe24:1, via swp3.100, weight 1
```
**Stage explained:** the border is the DC edge. It decapsulates VXLAN and forwards the
service IP out `swp3.100`, a **plain routed per-VRF subinterface** into the br-agg
backbone. From here on there is no VXLAN, just L3 in the `tenant-k8s` VRF.

### Stage 4 - `br-agg-sw-1` (backbone): L3 transit toward DC2
```
$ echo <pw> | sudo -S vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * fe80::5200:ff:fe1d:3, via swp5.100, weight 1
  * fe80::5200:ff:fe1e:3, via swp6.100, weight 1
```
**Stage explained:** the backbone routes the service, per tenant VRF, out `swp5.100` /
`swp6.100` (ECMP) toward the two DC2 borders. This is the east-west path: routed, no
firewall, no VXLAN.

### Stage 5 - DC2 `dc2-border-1`: backbone meets the DC2 overlay
```
$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
  * 10.2.0.11, via vlan3101_l3 onlink, weight 1
```
**Stage explained:** the DC2 border is the mirror of stage 3. It takes the routed
packet and puts it back into an overlay: the DC2 **L3VNI** `vlan3101_l3`, toward VTEP
`10.2.0.11` (dc2-k8s-leaf-1).

### Stage 6 - DC2 `dc2-k8s-leaf-1` (VTEP 10.2.0.11): to the pods
```
$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
  * 10.168.10.21, via vlan210, weight 1
  * 10.168.10.23, via vlan210, weight 1
```
**Stage explained:** the destination leaf. It decapsulates and delivers the service to
its **local** DC2 workers `10.168.10.21` / `.23` on `vlan210` (L2VNI 10210), where the
dc-demo pods answer. End of trace.

## The path, one line per hop
`DC1 worker-leaf (L3VNI)` -> `DC1 spine (underlay)` -> `DC1 border (overlay->routed)`
-> `br-agg backbone (routed, ECMP)` -> `DC2 border (routed->overlay)` ->
`DC2 spine (underlay)` -> `DC2 leaf (L3VNI)` -> `DC2 pods`.

## Gotchas learned doing this
- **Backbone sudo needs the password.** `sudo vtysh` on the key-only br-agg switches
  returns empty output unless you pipe the password: `echo <pw> | sudo -S vtysh -c ...`.
- **A MAC does not cross DCs.** L2VNI 10120 lives only on the worker-leaf pair. Trace a
  host across DCs by its IP (as a service), not its MAC.
- **Dual-homed MACs appear twice in BGP,** once per ES VTEP, both carrying the ESI.
  That is expected under EVPN Multihoming, not a duplicate.
- **ECMP is everywhere:** two DC1 borders, two backbone paths, two DC2 workers. A trace
  shows every equal-cost next hop, not a single line.
