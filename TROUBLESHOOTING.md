# EVPN-VXLAN fabric troubleshooting, line by line: NX-OS to Cumulus

The structured Nexus troubleshooting command set, translated **line by line** to this
Cumulus fabric, every command **run live** (2026-08-15) with its real output and an
explanation. Ordered the way you actually troubleshoot: underlay first, then overlay
control plane, then data plane, then host-level tracing. Two end-to-end workflows at the
end: a **MAC** and a **service**.

Reference objects used throughout:
- MAC `50:00:00:0e:00:01` = host `10.167.20.11` (DC1 k8s-worker-1), ESI-dual-homed to the
  `leaf-k8s-worker` pair. L2VNI 10120 (VLAN 120), L3VNI 50001.
- Service `192.168.202.2` = DC2 dc-demo LoadBalancer VIP, pods on dc2-k8s-worker-2/-3
  (nodes `10.168.10.22`/`.23`). DC2 L2VNI 10210, L3VNI 50101.
- VTEPs: worker-leaf pair `10.0.0.13`/`.14`, DC1 borders `10.0.0.17`/`.18`,
  DC2 leaf `10.2.0.11`, DC2 borders `10.2.0.15`/`.16`. VRF `tenant-k8s`.

**The one gotcha that will burn you first:** on Cumulus, `bridge fdb show` run
**unprivileged returns silently empty output**. Every FDB check below needs `sudo`.
(This looked like missing MACs until proven otherwise. It is a permission artifact.)

---

## 1. Underlay (eBGP IPv4 unicast)

The underlay's only job is loopback (VTEP) reachability. Line by line:

| NX-OS | Cumulus |
|---|---|
| `show ip interface brief` | `ip -br addr show` / `ip -br link show` |
| `show interface status` | `ip -br link show` (`l1-show` on real HW; n/a on VX) |
| `show interface eth1/x \| i "up\|down\|error"` | `ip -s link show swp1` |
| `show lldp neighbors` | `sudo lldpcli show neighbors ports swp1 summary` |
| `show ip bgp summary` / `show bgp ipv4 unicast summary` | `sudo vtysh -c "show bgp ipv4 unicast summary"` |
| `show bgp ipv4 unicast neighbors <spine-ip>` | `sudo vtysh -c "show bgp neighbors swp1"` (interface, not IP: unnumbered) |
| `show ip route bgp` | `sudo vtysh -c "show ip route bgp"` |
| `show ip route <remote-vtep>` | `sudo vtysh -c "show ip route 10.0.0.14"` |
| `show ip cef <prefix>` / `show forwarding route` | `ip route get 10.0.0.14` (kernel FIB) |
| MTU check | `ip link show swp1` |
| `ping <vtep> source <lo> df-bit packet-size 9000` | `ping -M do -s 9188 -I 10.0.0.13 10.0.0.14` |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo lldpcli show neighbors ports swp1 summary
Interface:    swp1, via: LLDP
    SysName:      spine-1
    PortDescr:    swp3
```
Cabling verification: our swp1 lands on spine-1 swp3. (Plain `lldpcli show neighbors
summary` also lists every box on eth0, because the lab mgmt network is one shared segment.)

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp ipv4 unicast summary"
Neighbor        V   AS     Up/Down  State/PfxRcd
spine-1(swp1)   4  65100   04:29:04     9
spine-2(swp2)   4  65100   04:29:08     9
Total number of neighbors 2

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp neighbors swp1"
BGP neighbor on swp1: fe80::5200:ff:fe01:3, remote AS 65100, local AS 65112, external link
Hostname: spine-1
  BGP version 4, remote router ID 10.0.0.1, local router ID 10.0.0.13
  BGP state = Established, up for 04:29:28
    ...
    Extended nexthop: advertised and received
    Address Family L2VPN EVPN: advertised and received
```
One session per spine, Established, prefixes received. Two things NX-OS people should
note: the neighbor is an **interface** (BGP unnumbered over the link-local fe80), and the
**Extended nexthop** capability is what lets IPv4 routes carry those fe80 next-hops.

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route bgp"
B>* 10.0.0.1/32  [20/0] via fe80::5200:ff:fe01:3, swp1, weight 1, 04:28:58
B>* 10.0.0.11/32 [20/0] via fe80::5200:ff:fe01:3, swp1, weight 1, 04:28:58
B>* 10.0.0.14/32 [20/0] via fe80::5200:ff:fe01:3, swp1, weight 1, 04:28:58
B>* 10.0.0.17/32 [20/0] via fe80::5200:ff:fe01:3, swp1, weight 1, 04:28:58
    ... (every VTEP loopback in both DCs)

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route 10.0.0.14"
Routing entry for 10.0.0.14/32
  Known via "bgp", distance 20, metric 0, best
  * fe80::5200:ff:fe01:3, via swp1, weight 1
  * fe80::5200:ff:fe02:3, via swp2, weight 1
```
Every remote VTEP loopback present; the per-prefix view shows **ECMP via both spines**.
One path only = check `maximum-paths` under the BGP address-family and the spine.

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip route get 10.0.0.14
10.0.0.14 via inet6 fe80::5200:ff:fe01:3 dev swp1 src 10.0.0.13
```
The `cef`/forwarding check: what the kernel FIB will actually do, including the source
address (our own VTEP).

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip link show swp1
3: swp1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9216 ...

cumulus@leaf-k8s-worker-1:mgmt:~$ ping -c2 -M do -s 9188 -I 10.0.0.13 10.0.0.14
2 packets transmitted, 2 received, 0% packet loss
rtt min/avg/max/mdev = 0.384/0.406/0.429/0.022 ms
```
Fabric links at 9216, and the money test: a **9216-byte df-bit ping VTEP to VTEP**
(9188 payload + 28 header), sourced from the local VTEP loopback. 0% loss = the underlay
carries max-size VXLAN frames end to end.

## 2. Overlay control plane (eBGP L2VPN EVPN)

| NX-OS | Cumulus |
|---|---|
| `show bgp l2vpn evpn summary` | `sudo vtysh -c "show bgp l2vpn evpn summary"` |
| `show bgp l2vpn evpn neighbors <spine>` | `sudo vtysh -c "show bgp neighbors swp1"` (EVPN AF section) |
| `show bgp l2vpn evpn` | `sudo vtysh -c "show bgp l2vpn evpn"` |
| `show bgp l2vpn evpn route-type 2` | `... route type macip` |
| `show bgp l2vpn evpn route-type 3` | `... route type multicast` |
| `show bgp l2vpn evpn route-type 5` | `... route type prefix` |
| (no NX-OS habit) Type-1 / Type-4 | `... route type ead` / `... route type es` (EVPN-MH) |
| `show bgp l2vpn evpn <mac-or-ip>` | `... route vni 10120 mac <mac> ip <ip>` |
| `show bgp l2vpn evpn vni-id <vni>` | `... route vni 10120` |
| spine `retain route-target all` / `next-hop-unchanged` | verify on the spine: next-hop must stay the leaf VTEP |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn summary"
spine-1(swp1)   4  65100  ... Established  128
spine-2(swp2)   4  65100  ... Established  128
```

The specific-host lookup (note: FRR needs **both** mac and ip for the exact-route form;
mac alone returns "% Network not in table". Grep `route type macip` as the fallback):
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route vni 10120 mac 50:00:00:0e:00:01 ip 10.167.20.11"
BGP routing table entry for [2]:[0]:[48]:[50:00:00:0e:00:01]:[32]:[10.167.20.11]
Paths: (3 available, best #1)
  Route [2]:...  VNI 10120/50001
  Local
    10.0.0.13 (leaf-k8s-worker-1) from 0.0.0.0 (10.0.0.13)
      ESI 03:44:38:39:be:ef:12:00:00:01 local-es peer-info: (active MM: 0)
      ... valid, sourced, local, best (EVPN local ES path)
      Extended Community: ET:8 RT:65000:10120 RT:65112:50001 Rmac:50:00:00:05:00:11
  Route [2]:...  Imported from 10.0.0.14:2:[2]:..., VNI 10120/50001
  65100 65112
    10.0.0.14 (spine-2) from spine-2(swp2) (10.0.0.2)
```
**Three paths for one host**: the local ES path (best), plus the same MAC/IP imported
from the ES peer VTEP `10.0.0.14` via each spine. `VNI 10120/50001` shows both the L2VNI
and the L3VNI in one route (symmetric IRB); the extended communities carry both RTs and
the router-mac. This one command answers "who owns this host and how does everyone
else reach it".

The EVPN-MH route types Cisco's vPC world does not have:
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route vni 10120" | head
*> [1]:[0]:[03:44:38:39:be:ef:12:00:00:01]:[32]:[10.0.0.13]:[0]     <- Type-1 Ethernet A-D
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type es" | grep -A1 be:ef:12
*> [4]:[03:44:38:39:be:ef:12:00:00:01]:[32]:[10.0.0.13] RD 10.0.0.13:3
      ET:8 ES-Import-Rt:44:38:39:be:ef:12 DF: (alg: 2, pref 32767)                <- Type-4 ES + DF election
```
Type-1 (Ethernet A-D) enables aliasing and fast withdraw per segment; Type-4 discovers
the ES peers and elects the DF (who forwards BUM). Together they replace clagd/vPC.

**The eBGP-over-eBGP transit check** (your failure class 1 and 2). On the spine, the
Type-2 next-hop must remain the originating leaf VTEP, and the RTs must survive:
```
cumulus@spine-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type macip" | grep -A2 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.13:2
      10.0.0.13 (leaf-k8s-worker-1)          0 65112 i
```
Next-hop `10.0.0.13` = the leaf, not the spine. FRR does not rewrite the next-hop for
EVPN transit and passes the extended communities through. Verified clean.

## 3. VXLAN data plane (the NVE / VNI layer)

| NX-OS | Cumulus |
|---|---|
| `show nve interface nve1 detail` | `ip -d link show vxlan48` |
| `show nve peers` | `sudo bridge fdb show dev vxlan48` (flood entries) + `show evpn vni <vni>` |
| `show nve vni` | `sudo vtysh -c "show evpn vni"` |
| `show interface nve1 counters` | `ip -s link show vxlan48` |
| `show nve vxlan-params` (UDP port) | `ip -d link show vxlan48` (`dstport`) |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -d link show vxlan48
    vxlan external vnifilter ... local 10.0.0.13 ... dstport 4789 nolearning ... neigh_suppress on

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn vni"
VNI      Type  VxLAN IF  # MACs  # ARPs  # Remote VTEPs  Tenant VRF   VLAN
10120    L2    vxlan48   5       6       1               tenant-k8s   120
50001    L3    vxlan99   5       5       n/a             tenant-k8s

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo bridge fdb show dev vxlan48
00:00:00:00:00:00 dev vxlan48 dst 10.0.0.14 src_vni 10120 self permanent      <- HER flood = the "nve peer"
50:00:00:06:00:11 dev vxlan48 dst 10.0.0.14 src_vni 10120 self extern_learn   <- a remote MAC via that VTEP
```
Cumulus 5.x uses a **single VXLAN device** (`vxlan external vnifilter`) for all L2VNIs,
plus one for the L3VNIs (`vxlan99`); UDP port 4789. The **`00:00:00:00:00:00 dst <vtep>
src_vni <vni>` flood entry is the peer list**: one per remote VTEP per VNI (HER =
head-end replication, from the Type-3 IMET route). A missing flood entry = no Type-3
from that VTEP (go back to section 2). `nolearning` + `extern_learn` entries = MACs are
programmed by BGP (zebra), not data-plane flooding.

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -s link show vxlan48
    RX:  bytes packets errors dropped   ->  1334318  21513  0  0
    TX:  bytes packets errors dropped   ->  1369669  22244  0  34
```

## 4. L2 forwarding and host learning

| NX-OS | Cumulus |
|---|---|
| `show mac address-table vlan <vlan>` | `sudo bridge fdb show \| grep "vlan 120"` |
| `show mac address-table address <mac>` | `sudo bridge fdb show \| grep <mac>` |
| `show l2route evpn mac all` | `sudo vtysh -c "show evpn mac vni all"` |
| `show l2route evpn mac-ip all` | `sudo vtysh -c "show evpn arp-cache vni all"` |
| `show l2route evpn mac evi <vlan> mac <mac> detail` | `sudo vtysh -c "show evpn mac vni 10120 mac <mac>"` |
| `show l2route fl all` (flood lists) | the `00:00:00:00:00:00` entries in `sudo bridge fdb show dev vxlan48` |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo bridge fdb show | grep 50:00:00:0e:00:01
50:00:00:0e:00:01 dev bond1 vlan 120 master br_default static

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(23) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Neighbors:  10.167.20.11 Active
```
The kernel FDB has the MAC **local on bond1** (`static` because zebra owns it), and
`show evpn mac` is the l2route bridge between BGP and the MAC table: local on its
attached leaf (with the ESI and `peer-active` sync), `remote` with the origin VTEP
everywhere else (see the `extern_learn ... dst 10.0.0.14` entry in section 3 for the
remote form). Remember: **`sudo`, or the FDB looks empty.**

## 5. L3 overlay (anycast gateway, symmetric IRB, VRFs)

| NX-OS | Cumulus |
|---|---|
| `show ip arp vrf <vrf>` | `ip -4 neigh show vrf tenant-k8s` |
| `show ip route vrf <vrf> <host>` | `sudo vtysh -c "show ip route vrf tenant-k8s <host>"` |
| `show bgp vrf <vrf> ipv4 unicast` | `sudo vtysh -c "show bgp vrf tenant-k8s ipv4 unicast"` |
| `show vrf` / `show vrf detail` | `sudo vtysh -c "show vrf"` ; `ip -d link show tenant-k8s` |
| `show fabric forwarding ip local-host-db` (HMM) | local entries in `show evpn arp-cache vni <vni>` |
| `show ip arp suppression-cache detail` | `sudo vtysh -c "show evpn arp-cache vni <vni>"` |
| `show interface vlan <svi>` + anycast config | `ip -br addr show vlan120` + `ip -br addr show vlan120-v0` |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -4 neigh show vrf tenant-k8s
10.167.20.11 dev vlan120     lladdr 50:00:00:0e:00:01 REACHABLE proto zebra      <- host ARP
10.0.0.17  dev vlan3001_l3 lladdr 50:00:00:09:00:11 extern_learn NOARP proto zebra  <- border rmac
10.0.0.11  dev vlan3001_l3 lladdr 50:00:00:03:00:11 extern_learn NOARP proto zebra  <- master-leaf rmac
```
Two kinds of neighbors: real host ARP on the SVI, and the **router-mac entries on the
L3VNI SVI** (`NOARP extern_learn`, programmed from EVPN, one per remote VTEP). Those rmac
entries are how symmetric IRB frames get their inner destination MAC.

**The remote-host /32 check** (the key Cisco check, a host on a different leaf pair):
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 10.167.10.11"
Routing entry for 10.167.10.11/32
  Known via "bgp", distance 20, metric 0, vrf tenant-k8s, best
  * 10.0.0.11, via vlan3001_l3 onlink, weight 1
  * 10.0.0.12, via vlan3001_l3 onlink, weight 1
```
A k8s master (on the master-leaf pair) appears here as a **/32 via BGP, next-hop = both
remote ES VTEPs, encap = the L3VNI** (`vlan3001_l3`). Exactly the NX-OS expectation, with
ECMP to the dual-homed pair for free.

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp vrf tenant-k8s ipv4 unicast" | head
*> 0.0.0.0/0        10.0.0.17(spine-2)<     *=  10.0.0.18(spine-1)<
*> 10.167.10.0/24   10.0.0.11(spine-2)<     *=  10.0.0.12(spine-1)<
*> 10.167.10.11/32  10.0.0.11(spine-2)<
```
The `<` marks routes **imported from EVPN** into the VRF; `*=` is a multipath.

```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -br addr show vlan120; ip -br addr show vlan120-v0
vlan120@br_default UP  10.167.20.2/24          <- this leaf's real SVI address
vlan120-v0@vlan120 UP  10.167.20.1/24          <- the VRR anycast gateway (same on both leaves)
```
The anycast gateway is the `-v0` VRR device: `.1` with a shared virtual MAC on every
leaf in the pair. Hosts point at `.1` and land on whichever leaf they hash to.

## 6. Border leaves / external connectivity

```
cumulus@leaf-border-1:mgmt:~$ sudo vtysh -c "show bgp vrf tenant-k8s ipv4 unicast summary"
Neighbor              V    AS     Up/Down   State/PfxRcd
br-agg-sw-1(swp3.100) 4   65400   04:55:02     11
br-agg-sw-2(swp4.100) 4   65400   04:55:04     11

cumulus@leaf-border-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 0.0.0.0/0"
  Known via "bgp", ... * fe80::5200:ff:fe25:1, via swp4.100

cumulus@leaf-border-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 10.80.15.50"
  (resolves to the default route above)
```
The border's per-VRF eBGP sessions run to the **backbone** (AS 65400); north-south
internet (the 10.80.15.x externals) rides the default toward the aggr, where the
firewalls attach. The border re-originates what it learns as EVPN Type-5 into the DC.

And the host-facing equivalent of "bgp vrf neighbors": on the k8s leaf, the **Cilium
node sessions** that inject the service VIPs into the fabric:
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp vrf tenant-k8s ipv4 unicast summary"
k8s-woker-1(10.167.20.11) 4  65010  ... 04:25:50   5
k8s-woker-2(10.167.20.12) 4  65010  ... 04:29:23   5
k8s-woker-3(10.167.20.13) 4  65010  ... 04:28:19   4
```

## 7a. Workflow: tracing the MAC end to end

1. **Is it local, and where?**
   `sudo bridge fdb show | grep 50:00:00:0e:00:01` -> `dev bond1 vlan 120 static`
2. **Its IP** - `ip -4 neigh show vrf tenant-k8s | grep 10.167.20.11` -> `50:00:00:0e:00:01 REACHABLE`
3. **Its EVPN advertisement, in full** -
   `show bgp l2vpn evpn route vni 10120 mac 50:00:00:0e:00:01 ip 10.167.20.11` ->
   3 paths: local ES (best) + imported from the ES peer via each spine (section 2).
4. **The ES peer** - on leaf-k8s-worker-2: `show evpn mac vni 10120 mac ...` -> local on
   its bond1, same ESI. `show evpn es` -> `LR` with peer VTEP `10.0.0.14`.
5. **How a remote MAC looks** - `sudo bridge fdb show dev vxlan48` ->
   `50:00:00:06:00:11 dst 10.0.0.14 extern_learn` (BGP-programmed, VTEP next-hop).
6. **Underlay to the peer VTEP** - `show ip route 10.0.0.14` (ECMP both spines) +
   the 9216-byte df-bit ping (section 1). 
7. **Counters** - `ip -s link show vxlan48` for encap drops; `ip -s link show swp1` for wire errors.

Scope: L2VNI 10120 exists only on this leaf pair, so the MAC never appears in DC2.
Cross-DC, you trace the host's IP (workflow 7b).

## 7b. Workflow: tracing the service end to end (DC1 client toward DC2)

1. **What is it, in Kubernetes** - `kd2 -n demo get svc,endpoints,pods -o wide` ->
   VIP `192.168.202.2:80`, nodePort 32077, pods `10.245.1.47`/`10.245.3.240` on nodes
   `10.168.10.23`/`.22`. Cilium: `routing-mode tunnel`, BGP on, `externalTrafficPolicy Local`.
2. **Where it enters the fabric** - the Cilium BGP sessions on the DC2 leaf (section 6
   pattern, AS 65020 in DC2): only pod-bearing nodes advertise the VIP.
3. **Ingress leaf lookup (DC1)** - `show ip route vrf tenant-k8s 192.168.202.2` ->
   `10.0.0.17 / 10.0.0.18 via vlan3001_l3 onlink` (both DC1 border VTEPs, ECMP).
4. **The control plane behind it** - `show bgp l2vpn evpn route type prefix | grep -A2 202.2]` ->
   Type-5 from RD `10.0.0.17:4`, AS-path `65100 65114 65400 65213 65200 65211 65020`
   (spine, border, backbone, dc2-border, dc2-spine, dc2-leaf, dc2-nodes: the whole fabric
   in one path), `Rmac:50:00:00:09:00:11`.
5. **Underlay to the border VTEP** - `show ip route 10.0.0.17` -> ECMP via both spines.
6. **Border** - sessions to both br-agg Established (section 6); the backbone routes the
   VIP per-VRF, ECMP `swp5.100`/`swp6.100` to both DC2 borders.
7. **DC2 border** - `show ip route vrf tenant-k8s 192.168.202.2` -> `10.2.0.11 via
   vlan3101_l3` (re-encap into the DC2 L3VNI toward the DC2 leaf VTEP).
8. **Egress leaf (DC2)** - route -> `10.168.10.22 / 10.168.10.23 via vlan210` (exactly the
   pod nodes); `show evpn arp-cache vni 10210` -> `10.168.10.22 -> 50:00:00:21:00:01`;
   `sudo bridge fdb show | grep 50:00:00:21:00:01` -> `dev bond3 vlan 210` (the node's bond).
9. **Live verification, from the ingress leaf, in the VRF**:
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo ip vrf exec tenant-k8s curl -s -o /dev/null -w "HTTP %{http_code}\n" http://192.168.202.2/
HTTP 200

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo ip vrf exec tenant-k8s ping -c3 192.168.202.2
3 packets transmitted, 0 received, +3 errors, 100% packet loss

cumulus@leaf-k8s-worker-1:mgmt:~$ sudo ip vrf exec tenant-k8s traceroute -n -w1 -q1 -m8 192.168.202.2
 1  192.0.0.8    0.496 ms      <- DC1 border (unnumbered hop, RFC 8155 dummy address)
 2  10.201.20.1  0.804 ms      <- br-agg-sw-1 (backbone)
 3  192.0.0.8    0.885 ms      <- DC2 border (unnumbered hop)
 4  10.168.10.2  1.725 ms      <- dc2-k8s-leaf SVI (vlan210)
 5  *                          <- the VIP itself: no UDP/ICMP answer (by design)
```
Three lessons in one screen. **curl succeeds, ping fails**: a Kubernetes LoadBalancer
VIP answers its service port (TCP:80) and nothing else; ICMP loss to a VIP is not an
outage, so test TCP. The **traceroute walks the exact device path** we traced hop by
hop; unnumbered routed hops answer as `192.0.0.8` (the IPv4 dummy address FRR uses when
an interface has no IPv4 of its own), and the last responding hop is the DC2 leaf SVI
before the VIP goes silent.

## 8. Fabric-wide health snapshot

| NX-OS | Cumulus |
|---|---|
| `show bgp all summary` | `sudo vtysh -c "show bgp summary"` (all AFs + VRFs) |
| `show nve peers \| count` | `sudo bridge fdb show dev vxlan48 \| grep -c 00:00:00:00:00:00` |
| `show nve vni \| i Up \| count` | `sudo vtysh -c "show evpn vni"` (2 VNIs on this leaf) |
| `show interface counters errors non-zero` | `ip -s link show swp1` (errors 0) |
| `show logging last 50` | `sudo tail -50 /var/log/frr/frr.log` |
| `show spanning-tree summary` | `sudo mstpctl showbridge br_default` (host ports only; the fabric is routed) |
| `show port-channel summary` / `show vpc` | `sudo vtysh -c "show evpn es"` (3 segments, all `LR`) |
| `show hardware capacity forwarding` | n/a on VX (no ASIC); real HW: `nv show platform` |
| `show consistency-checker l2` | n/a on VX; real HW: `cl-support` bundle for TAC |

```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo tail -2 /var/log/frr/frr.log
bgpd: %ADJCHANGE: neighbor 10.167.20.13(k8s-woker-3) in vrf tenant-k8s Up
bgpd: %ADJCHANGE: neighbor 10.167.20.11(k8s-woker-1) in vrf tenant-k8s Up
```
Even the log tail is a health check here: the most recent adjacency changes are the
Cilium node sessions, which flap whenever nodes or pods move. Fabric adjacencies
(spines) have been up for hours.

## The three eBGP-over-eBGP failure classes, verified

1. **Spine rewriting next-hop** - checked on spine-1: the Type-2 next-hop stayed
   `10.0.0.13` (the leaf). Clean.
2. **Spine dropping route-targets** - the RTs (`65000:10120`, `65112:50001`) and Rmac
   arrive intact on the other leaves. Clean.
3. **Underlay ECMP asymmetry** - every VTEP loopback shows two spine paths, and the
   9216-byte df-bit ping passes. Clean.

Plus the two Cumulus-specific traps: `bridge fdb show` **must run under sudo**, and on
the key-only backbone switches `sudo vtysh` needs the password piped or output comes
back empty.

## Case: client can ping IPs but names fail intermittently (fixed 2026-08-15)

Reported on client-1: "can ping the IP but not the DNS". Diagnosis, in order:

1. **Resolver state** - `resolvectl status` showed **two DNS scopes and no routing
   domains**: mgmt link with public servers (8.8.8.8 / 1.1.1.1), lab link with the lab
   BIND VIP (`10.80.15.41`). systemd-resolved raced `.lab` queries across both; a fast
   public **NXDOMAIN beat the lab server's positive answer** some of the time. That is
   why it was intermittent and self-recovering.
2. **Reachability of the DNS VIP** - ping and tcp/53 to `10.80.15.41` were clean, and
   `dig @10.80.15.41` answered: the server and the fabric path were healthy. (Earlier in
   the day the tenant-svc path had real blips during the EVPN-MH work; resolved would
   have marked the lab server bad and leaned on the public servers, which can never
   resolve `.lab` - the visible episode.)
3. **Fix (both clients, .89 and .96), persistent and renderer-agnostic**:
```
# /etc/systemd/resolved.conf.d/90-ecloud-lab.conf
[Resolve]
DNS=10.80.15.41
Domains=~ecloud.lab ~lab
# then: systemctl restart systemd-resolved
```
4. **Verify** - `resolvectl status` Global scope shows `DNS Domain: ~ecloud.lab ~lab`;
   15/15 rapid `dig demo.apps.ecloud.lab` return `10.80.15.50`; `dig google.com` still
   answers (public path untouched); end-to-end `curl http://demo.apps.ecloud.lab/api/whoami`
   returns the serving pod.

Two traps from doing it: piping content into `sudo -S tee` writes an **empty file**
(the pipe feeds sudo's password prompt) - use `echo <pw> | sudo -S bash -c 'printf ... > file'`
and read the file back. And a split-scope resolver failure looks exactly like a fabric
problem from the application's point of view; check `resolvectl status` for missing
routing domains before tracing the network.
