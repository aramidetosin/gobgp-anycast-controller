# Tracing a MAC or a service across the fabric: a full L1 to L7 walk

How to follow traffic through the ecloud two-DC fabric end to end, one layer and one
device at a time, with the exact command to run, its real output, and a plain-language
explanation of what is happening. Two examples: a **MAC** (stays inside one DC, layers
1 to 2) and a **service** (crosses both DCs, layers 1 to 7). Everything below was
captured live on the running fabric on 2026-08-15. Nothing is invented.

![L1-L7 trace diagram](diagrams/trace-l1-l7.png)

---

## For executives (read this first)

A user request to an app travels **down** the network stack on one server, **across**
the network, and **up** the stack on another server in the other data center. Three
things make this fabric resilient and fast, and a trace shows all three:

1. **Everything is doubled.** Every server has two uplinks to two switches; every path
   between data centers has two routes. There is no single wire or box whose failure
   drops the service. A trace shows two next-hops at almost every step (that is "ECMP").
2. **The network is layered like an onion.** The application's traffic rides inside the
   Kubernetes pod network, which rides inside the data-center fabric network, which
   rides on a plain routed core. Each layer is independent, so we can change one without
   touching the others (that is how we swapped the redundancy design with zero outage).
3. **A service is reachable from either data center.** The same app answers in both
   DCs; the network delivers a request to whichever DC is chosen, over a routed backbone.

The rest of this document is the engineer's version: the commands and the output.

---

## The mental model: layers, underlay, and two overlays

The fabric stacks three networks on top of each other. Reading a trace means knowing
which layer you are looking at.

| Layer | What carries it here | Key command |
|---|---|---|
| **L1 physical** | leaf `swpN` to server NIC, a bonded (LACP) link | `ethtool <swp>`, `cat /sys/class/net/<swp>/carrier` |
| **L2 data link** | the LACP **bond** = an EVPN **Ethernet Segment (ESI)**; a VLAN | `cat /proc/net/bonding/<bond>`, `bridge fdb show` |
| **L2 overlay (fabric)** | Cumulus **VXLAN L2VNI**, EVPN **Type-2** (MAC/IP) | `show evpn mac vni <l2vni> mac <MAC>` |
| **L3 overlay (fabric)** | **L3VNI** (symmetric IRB), EVPN **Type-5** (IP prefix) | `show ip route vrf <tenant> <IP>` |
| **L3 underlay** | the loopback **VTEP** IPs, spine BGP (the transport the overlay rides on) | `show ip route <VTEP>` |
| **pod overlay (k8s)** | **Cilium VXLAN** (pod IPs `10.245.x`), Cilium BGP advertising the service VIP | `kubectl ...`, `cilium-dbg bgp routes` |
| **L4 to L7** | TCP:80 to the service VIP, the HTTP app | `curl`, `kubectl get svc/endpoints/pods` |

**The two-overlay point.** The Kubernetes pod network is its own VXLAN overlay (Cilium,
`routing-mode: tunnel`, pods in `10.245.0.0/16`). It rides on the **node** network
(`10.167/10.168`), which is itself an overlay: the Cumulus **fabric VXLAN** (EVPN). That
in turn rides on the plain routed **underlay** (loopbacks `10.0.0.x` / `10.2.0.x`, spine
BGP). So a cross-DC packet is encapsulated by the fabric, routed plainly across the
backbone, and re-encapsulated in the far DC. Confirmed live: `cilium-config
routing-mode=tunnel tunnel-protocol=vxlan`, and the fabric `vxlan48 ... dstport 4789`.

Tenant VRF in both examples: `tenant-k8s`.
DC1: L2VNI **10120** (VLAN 120), L3VNI **50001**, VTEPs `10.0.0.x`.
DC2: L2VNI **10210** (VLAN 210), L3VNI **50101**, VTEPs `10.2.0.x`.

---

## Trace A: a MAC (L1 to L2), within DC1

Target `50:00:00:0e:00:01`: the `data0` NIC of DC1 **k8s-worker-1** (host
`10.167.20.11`), dual-homed to the worker-leaf pair by EVPN Multihoming. Watch the ESI
thread through every layer.

### L1 physical: the wire and the bond member
```
$ cat /proc/net/bonding/bond1 | grep -E "Slave Interface|MII Status|Aggregator ID"
Slave Interface: swp3
MII Status: up
Aggregator ID: 1
$ cat /sys/class/net/swp3/carrier      -> 1
```
**Explanation:** the host attaches to leaf port `swp3`, which is a member of `bond1`.
Carrier is up (L1 good). In this lab the physical link is an EVE-NG virtual wire; on
hardware it is a cable, but the commands are identical.

### L2 data link: the LACP bond is an Ethernet Segment (ESI)
The bond runs 802.3ad (LACP). Under EVPN Multihoming the bond is an **Ethernet Segment**;
the leaf presents the ES system MAC (`44:38:39:be:ef:12`, the old clag MAC reused) as its
LACP system-id, so the host bonds to both leaves as if to one.

### L2 overlay: the MAC in the fabric VXLAN L2VNI
```
$ vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(23) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Neighbors:  10.167.20.11 Active
```
**Explanation:** the MAC is *local* on `bond1`, VLAN 120 (which maps to L2VNI 10120). It
belongs to Ethernet Segment `03:44:38:39:be:ef:12:00:00:01`, is `peer-active` (synced
with the ES peer over EVPN, not a peerlink), and its host IP is `10.167.20.11`.

The **same** command on the peer leaf (`worker-leaf-2`, VTEP 10.0.0.14) shows the MAC
local on *its* `bond1(22)` under the *same* ESI. That is the multihoming: both leaves own
the segment, no peerlink.

### The fabric VXLAN encapsulation
```
$ ip -d link show vxlan48
vxlan48 ... mtu 9216 master br_default
   vxlan external ... local 10.0.0.13 ... dstport 4789 ... neigh_suppress on
```
**Explanation:** the L2VNI 10120 is carried by VXLAN device `vxlan48`, sourced from this
leaf's VTEP `10.0.0.13`, UDP dest port 4789, jumbo MTU 9216, ARP suppression on. This is
the encapsulation a remote leaf uses to reach the MAC.

### L2 control plane: the EVPN Type-2 route
```
$ vtysh -c "show bgp l2vpn evpn route type macip" | grep -A3 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.13:2
      10.0.0.13 (leaf-k8s-worker-1)  ESI:03:44:38:39:be:ef:12:00:00:01  RT:65000:10120
*> [2]:[0]:[48]:[50:00:00:0e:00:01]:[32]:[10.167.20.11] RD 10.0.0.13:2
      ...  RT:65000:10120 RT:65112:50001 Rmac:50:00:00:05:00:11
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.14:2  (leaf-k8s-worker-2)
```
**Explanation:** the MAC-only Type-2 (L2 bridging, `RT:65000:10120`) and the MAC+IP
Type-2 (adds the host IP, carries the L3VNI target `RT:65112:50001` and the router-mac
for symmetric IRB). Advertised from **both** ES VTEPs (10.0.0.13 and 10.0.0.14), each
stamped with the ESI, so any remote leaf ECMPs to both.

**Scope:** L2VNI 10120 lives only on the worker-leaf pair, so this MAC never reaches DC2.
To follow the host across DCs you trace its IP as a service, next.

---

## Trace B: a service (L1 to L7), DC1 to DC2

Target `192.168.202.2`: the DC2 regional LoadBalancer VIP for the `dc-demo` app. We start
at the top (what the user sees), drop down through Kubernetes to where the VIP enters the
fabric, follow the packet across the fabric, and come up the stack at the far end.

### L7 / L4: the app and the service (Kubernetes)
```
$ curl http://192.168.202.2/            ->  <title>DC2 demo</title>   (HTTP 200)

$ kubectl --context dc2 -n demo get svc dc-demo-dc2 -o wide
NAME          TYPE           CLUSTER-IP      EXTERNAL-IP     PORT(S)        SELECTOR
dc-demo-dc2   LoadBalancer   10.96.225.71    192.168.202.2   80:32077/TCP   app=dc-demo

$ kubectl --context dc2 -n demo get endpoints dc-demo-dc2
dc-demo-dc2   10.245.2.51:8080,10.245.3.212:8080

$ kubectl --context dc2 -n demo get pods -o wide
dc-demo-...-7v289   Running   10.245.2.51    dc2-k8s-worker-1
dc-demo-...-jl86x   Running   10.245.3.212   dc2-k8s-worker-2

$ kubectl --context dc2 get nodes -o wide
dc2-k8s-worker-1   Ready   10.168.10.21
dc2-k8s-worker-2   Ready   10.168.10.22
```
**Explanation:** the service VIP `192.168.202.2` (a `LoadBalancer`, port 80 to nodePort
32077) is backed by two pods (`10.245.2.51`, `10.245.3.212`, listening on 8080) on nodes
`dc2-k8s-worker-1` / `-2` (node IPs `10.168.10.21` / `.22`). The pod IPs are on Cilium's
pod overlay; the node IPs are on the fabric.

### The Kubernetes to fabric handoff (Cilium BGP)
```
$ kubectl --context dc2 -n kube-system get cm cilium-config -o jsonpath='{.data.routing-mode} {.data.enable-bgp-control-plane}'
tunnel  true
$ kubectl --context dc2 -n demo get svc dc-demo-dc2 -o jsonpath='{.spec.externalTrafficPolicy}'
Local
$ kubectl --context dc2 get ciliumbgpadvertisements
NAME     AGE
lb-vip   ...
```
**Explanation:** Cilium's BGP control plane advertises the LoadBalancer VIP into the
fabric. Because `externalTrafficPolicy: Local`, only nodes that actually run a pod
advertise the VIP, so the fabric's next-hops for `.202.2` are exactly the worker nodes
holding the pods. This is where the k8s world hands the route to the Cumulus world: each
worker node BGP-peers with the DC2 leaf and announces `192.168.202.2`.

### The fabric walk (same command each hop points to the next device)
`vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"`

**Stage 1 - DC1 worker-leaf (L3 overlay): into the L3VNI toward the DC1 borders**
```
* 10.0.0.17, via vlan3001_l3 onlink      # leaf-border-1 VTEP
* 10.0.0.18, via vlan3001_l3 onlink      # leaf-border-2 VTEP (ECMP)
```
The service lives in DC2, so this leaf resolves it through the **L3VNI** SVI
`vlan3001_l3` (symmetric IRB) toward the two DC1 border VTEPs, and VXLAN-encapsulates
toward one of them.

**Stage 2 - DC1 spine (L3 underlay): carry the tunnel to the border VTEP**
```
$ vtysh -c "show ip route 10.0.0.17"
* fe80::5200:ff:fe09:1, via swp7
```
The spine has no VTEP. It routes the encapsulated packet by its **outer** destination
(the border VTEP `10.0.0.17`) out `swp7`. It never looks inside the tunnel. Pure underlay.

**Stage 3 - DC1 border (overlay to routed): decapsulate, hand to the backbone**
```
* fe80::5200:ff:fe24:1, via swp3.100
```
The border is the DC edge. It decapsulates VXLAN and forwards the service IP out
`swp3.100`, a plain routed per-VRF subinterface into the backbone. No more VXLAN from here.

**Stage 4 - br-agg backbone (L3 routed): east-west to DC2 (ECMP)**
```
$ echo <pw> | sudo -S vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
* fe80::5200:ff:fe1d:3, via swp5.100     # dc2-border-1
* fe80::5200:ff:fe1e:3, via swp6.100     # dc2-border-2 (ECMP)
```
Plain L3 routing, per tenant VRF, ECMP toward both DC2 borders. No firewall, no VXLAN.
(On the key-only backbone switches `sudo` needs the password piped, or FRR returns empty.)

**Stage 5 - DC2 border (routed to overlay): re-encapsulate into DC2**
```
* 10.2.0.11, via vlan3101_l3 onlink
```
The mirror of stage 3: the DC2 border puts the routed packet back into an overlay, the
DC2 **L3VNI** `vlan3101_l3`, toward VTEP `10.2.0.11` (dc2-k8s-leaf-1).

**Stage 6 - DC2 spine (underlay):** routes by the outer VTEP `10.2.0.11`, same as stage 2.

**Stage 7 - DC2 leaf (overlay to L2): deliver to the node**
```
* 10.168.10.21, via vlan210
* 10.168.10.22, via vlan210
```
The destination leaf decapsulates and delivers the service to its **local** DC2 workers
`10.168.10.21` / `.22` on `vlan210` (L2VNI 10210). These are the nodes running the pods.

### Back up the stack at DC2
The node receives the packet for VIP `192.168.202.2:80`. kube-proxy / Cilium DNATs it to
a backing pod (`10.245.2.51:8080` or `10.245.3.212:8080`) over the **Cilium pod overlay**,
the pod's HTTP handler answers, and the reply retraces the path. `curl` sees `DC2 demo`.

### The path, one line per hop
`DC1 worker-leaf (L3VNI) -> DC1 spine (underlay) -> DC1 border (overlay to routed) ->
br-agg backbone (routed, ECMP) -> DC2 border (routed to overlay) -> DC2 spine (underlay)
-> DC2 leaf (L3VNI) -> DC2 node -> pod -> app`.

---

## Every command, by layer and device

| Layer | Where | Command |
|---|---|---|
| L1 | leaf | `ethtool <swp>` ; `cat /sys/class/net/<swp>/carrier` |
| L2 bond/ESI | leaf | `cat /proc/net/bonding/<bond>` |
| L2 MAC | any leaf in the L2VNI | `vtysh -c "show evpn mac vni <l2vni> mac <MAC>"` |
| L2 Type-2 | leaf / spine (RR) | `vtysh -c "show bgp l2vpn evpn route type macip" \| grep <MAC>` |
| VXLAN encap | leaf | `ip -d link show <vxlan-dev>` ; `bridge fdb show` |
| L3 route | every leaf/border/backbone hop | `vtysh -c "show ip route vrf <tenant> <IP>"` |
| L3 Type-5 | leaf / border | `vtysh -c "show bgp l2vpn evpn route type prefix" \| grep <IP>` |
| underlay | spine | `vtysh -c "show ip route <VTEP>"` |
| k8s service | cluster | `kubectl -n <ns> get svc,endpoints,pods -o wide` |
| k8s nodes | cluster | `kubectl get nodes -o wide` |
| CNI / BGP | cluster | `kubectl -n kube-system get cm cilium-config -o yaml` ; `cilium-dbg bgp routes` |
| L7 | client | `curl http://<vip>/` |

## Gotchas learned doing this
- **Backbone sudo needs the password.** `sudo vtysh` on the key-only br-agg switches
  returns empty unless you pipe the password: `echo <pw> | sudo -S vtysh -c "..."`.
  My first backbone reads looked like missing routes until I fixed that.
- **A MAC does not cross DCs.** L2VNI 10120 lives only on the worker-leaf pair. Trace a
  host across DCs by its IP (as a service), not its MAC.
- **Two overlays, not one.** A pod-to-pod cross-DC packet is encapsulated by Cilium
  (pod overlay) *and* by the fabric (EVPN VXLAN). A client-to-VIP packet only rides the
  fabric overlay until the last hop, where Cilium delivers to the pod.
- **Dual-homed MACs appear twice in BGP,** once per ES VTEP, both carrying the ESI. That
  is expected under EVPN Multihoming, not a duplicate.
- **ECMP is everywhere:** two borders, two backbone paths, two workers. A trace shows
  every equal-cost next hop.
