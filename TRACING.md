# Tracing a MAC or a service across the fabric: a full L1 to L7 walk

How to follow traffic through the ecloud two-DC fabric end to end, one layer and one
device at a time. Each command below is shown **as it is run on the device** (the real
shell prompt is included, so you can see exactly where every capture came from), followed
by its real output and a plain-language explanation. Two examples: a **MAC** (stays inside
one DC, layers 1 to 2) and a **service** (crosses both DCs, layers 1 to 7). Everything was
captured live on the running fabric on 2026-08-15.

![L1-L7 trace diagram](diagrams/trace-l1-l7.png)

---

## For executives (read this first)

A user request travels **down** the stack on one server, **across** a layered network, and
**up** the stack on a server in the other data center. Three things make it resilient:

1. **Everything is doubled** (two uplinks per server, two paths between DCs). A trace shows
   two next-hops at almost every step (ECMP). No single wire or box is in the path.
2. **The network is layered like an onion.** The app rides inside the Kubernetes pod
   network, which rides inside the data-center fabric, which rides on a plain routed core.
3. **The service answers in both DCs.** The network delivers a request to whichever DC is
   chosen, over a routed backbone.

The rest is the engineer's version: the prompt, the command, the output.

---

## The layers, and the two overlays

| Layer | What carries it here | Key command |
|---|---|---|
| **L1 physical** | leaf `swpN` to server NIC (bonded / LACP) | `ethtool <swp>` ; `cat /sys/class/net/<swp>/carrier` |
| **L2 data link** | LACP **bond** = EVPN **Ethernet Segment (ESI)**; a VLAN | `cat /proc/net/bonding/<bond>` |
| **L2 overlay (fabric)** | Cumulus **VXLAN L2VNI**, EVPN **Type-2** | `show evpn mac vni <l2vni> mac <MAC>` |
| **L3 overlay (fabric)** | **L3VNI** (symmetric IRB), EVPN **Type-5** | `show ip route vrf <tenant> <IP>` |
| **L3 underlay** | loopback **VTEPs**, spine BGP transport | `show ip route <VTEP>` |
| **pod overlay (k8s)** | **Cilium VXLAN** (pods `10.245.x`), Cilium BGP advertising the VIP | `kubectl ...` ; `cilium-dbg bgp routes` |
| **L4 to L7** | TCP:80 to the service VIP, the HTTP app | `curl` ; `kubectl get svc,endpoints,pods` |

**The two-overlay point.** The Kubernetes pod network is its own VXLAN overlay (Cilium,
`routing-mode: tunnel`, pods `10.245.0.0/16`) riding on the node network, which is itself
the Cumulus **fabric VXLAN** (EVPN), riding on the plain routed **underlay** (loopbacks
`10.0.0.x` / `10.2.0.x`). Confirmed live: `cilium-config routing-mode=tunnel`, fabric
`vxlan48 ... dstport 4789`.

Tenant VRF: `tenant-k8s`. DC1 L2VNI **10120** (VLAN 120), L3VNI **50001**, VTEPs `10.0.0.x`.
DC2 L2VNI **10210** (VLAN 210), L3VNI **50101**, VTEPs `10.2.0.x`.

---

## Trace A: a MAC (L1 to L2), within DC1

Target `50:00:00:0e:00:01` = the data0 NIC of DC1 **k8s-worker-1** (host `10.167.20.11`),
dual-homed to the worker-leaf pair by EVPN Multihoming.

### L1 + L2: the wire and the bond, on the leaf the host attaches to
```
cumulus@leaf-k8s-worker-1:mgmt:~$ grep -E "Slave Interface|MII Status|Aggregator ID" /proc/net/bonding/bond1
MII Status: up
Slave Interface: swp3
MII Status: up
Aggregator ID: 1
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Explanation:** the host attaches to leaf port `swp3` (L1), a member of `bond1` (L2, LACP
802.3ad). Under EVPN-MH the bond is an Ethernet Segment; the leaf presents the ES system
MAC (`44:38:39:be:ef:12`) as its LACP system-id, so the host bonds to both leaves as one.

### L2 overlay: the MAC in the fabric VXLAN L2VNI
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(23) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Local Seq: 0 Remote Seq: 0
 Uptime: 01:44:21
 Neighbors:
    10.167.20.11 Active
    fe80::5200:ff:fe0e:1 Active
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Explanation:** local on `bond1`, VLAN 120 (L2VNI 10120), Ethernet Segment
`03:44:38:39:be:ef:12:00:00:01`, `peer-active` (synced with the ES peer over EVPN, no
peerlink), host IP `10.167.20.11`.

### The same MAC is local on the ES peer too (the multihoming)
```
cumulus@leaf-k8s-worker-2:mgmt:~$ sudo vtysh -c "show evpn mac vni 10120 mac 50:00:00:0e:00:01"
MAC: 50:00:00:0e:00:01
 ESI: 03:44:38:39:be:ef:12:00:00:01
 Intf: bond1(22) VLAN: 120
 Sync-info: neigh#: 2 peer-active
 Neighbors:
    10.167.20.11 Active
cumulus@leaf-k8s-worker-2:mgmt:~$
```
**Explanation:** same MAC, same ESI, local on the peer leaf's own `bond1`. Both leaves own
the segment: that is EVPN Multihoming.

### The fabric VXLAN encapsulation
```
cumulus@leaf-k8s-worker-1:mgmt:~$ ip -d link show vxlan48
    vxlan external ... local 10.0.0.13 srcport 0 0 dstport 4789 nolearning ttl 64 ...
    ... neigh_suppress on ...
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Explanation:** L2VNI 10120 rides VXLAN device `vxlan48`, sourced from this leaf's VTEP
`10.0.0.13`, UDP 4789, ARP suppression on.

### L2 control plane: the EVPN Type-2 route, from BOTH VTEPs
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show bgp l2vpn evpn route type macip" | grep -A2 0e:00:01
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.13:2
      10.0.0.13 (leaf-k8s-worker-1)  ESI:03:44:38:39:be:ef:12:00:00:01  RT:65000:10120
*> [2]:[0]:[48]:[50:00:00:0e:00:01]:[32]:[10.167.20.11] RD 10.0.0.13:2
      ...  RT:65000:10120 RT:65112:50001 Rmac:50:00:00:05:00:11
*> [2]:[0]:[48]:[50:00:00:0e:00:01] RD 10.0.0.14:2   (leaf-k8s-worker-2)
cumulus@leaf-k8s-worker-1:mgmt:~$
```
**Explanation:** the MAC-only Type-2 (L2, `RT:65000:10120`) and the MAC+IP Type-2 (adds the
host IP, carries the L3VNI target `RT:65112:50001` and the router-mac for symmetric IRB).
Advertised from both ES VTEPs, each stamped with the ESI, so any remote leaf ECMPs to both.
This MAC never leaves DC1 (L2VNI 10120 is only on this pair).

---

## Trace B: a service (L1 to L7), DC1 to DC2

Target `192.168.202.2` = the DC2 dc-demo LoadBalancer VIP. Start at the top, drop through
Kubernetes to where the VIP enters the fabric, walk the fabric, and come up the stack in DC2.

### L7 / L4 / k8s: the app and the service
The Kubernetes commands run from the operator workstation via the SOCKS tunnel; `kd2` is
`kubectl --context dc2`.
```
you@ops:~$ curl http://192.168.202.2/
<!doctype html> ... <title>DC2 demo</title> ...          (HTTP 200)

you@ops:~$ kd2 -n demo get svc dc-demo-dc2 -o wide
NAME          TYPE           CLUSTER-IP     EXTERNAL-IP     PORT(S)        SELECTOR
dc-demo-dc2   LoadBalancer   10.96.225.71   192.168.202.2   80:32077/TCP   app=dc-demo

you@ops:~$ kd2 -n demo get endpoints dc-demo-dc2
dc-demo-dc2   10.245.1.47:8080,10.245.3.240:8080

you@ops:~$ kd2 -n demo get pods -l app=dc-demo -o wide
dc-demo-...-tv62r   Running   10.245.1.47    dc2-k8s-worker-3
dc-demo-...-n9fgm   Running   10.245.3.240   dc2-k8s-worker-2
```
**Explanation:** the VIP `192.168.202.2` (LoadBalancer, port 80) is backed by two pods
(`10.245.1.47`, `10.245.3.240`, on 8080) on nodes `dc2-k8s-worker-3` / `-2` (node IPs
`10.168.10.23` / `.22`). Pod IPs are on Cilium's pod overlay; node IPs are on the fabric.
Pod placement is dynamic: pods reschedule, and the VIP advertisement follows them (next).

### The Kubernetes to fabric handoff (Cilium BGP)
```
you@ops:~$ kd2 -n kube-system get cm cilium-config -o jsonpath='{.data.routing-mode} bgp={.data.enable-bgp-control-plane}'
tunnel bgp=true
you@ops:~$ kd2 -n demo get svc dc-demo-dc2 -o jsonpath='{.spec.externalTrafficPolicy}'
Local
```
**Explanation:** Cilium's BGP control plane advertises the VIP into the fabric. Because
`externalTrafficPolicy: Local`, only nodes running a pod advertise it, so the fabric's
next-hops for `.202.2` are exactly the pod-bearing workers (here `.22` and `.23`). This is
where a Kubernetes route becomes a Cumulus fabric route.

### The fabric walk (same command each hop points to the next device)

**Stage 1 - DC1 worker-leaf (L3 overlay): into the L3VNI toward the DC1 borders**
```
cumulus@leaf-k8s-worker-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  Known via "bgp", distance 20, metric 0, vrf tenant-k8s, best
  * 10.0.0.17, via vlan3001_l3 onlink, weight 1     # leaf-border-1 VTEP
  * 10.0.0.18, via vlan3001_l3 onlink, weight 1     # leaf-border-2 VTEP (ECMP)
cumulus@leaf-k8s-worker-1:mgmt:~$
```
The service is remote, so the leaf resolves it through the L3VNI SVI `vlan3001_l3` and
VXLAN-encapsulates toward a DC1 border VTEP.

**Stage 2 - DC1 spine (L3 underlay): carry the tunnel by its outer VTEP**
```
cumulus@spine-1:mgmt:~$ sudo vtysh -c "show ip route 10.0.0.17"
Routing entry for 10.0.0.17/32
  * fe80::5200:ff:fe09:1, via swp7
cumulus@spine-1:mgmt:~$
```
The spine has no VTEP. It routes the encapsulated packet by its outer destination (the
border VTEP `10.0.0.17`) out `swp7`, never looking inside.

**Stage 3 - DC1 border (overlay to routed): decapsulate, hand to the backbone**
```
cumulus@leaf-border-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * fe80::5200:ff:fe24:1, via swp3.100, weight 1
cumulus@leaf-border-1:mgmt:~$
```
The border decapsulates VXLAN and forwards out `swp3.100`, a plain routed per-VRF
subinterface into the backbone. No more VXLAN.

**Stage 4 - br-agg backbone (L3 routed): east-west to DC2 (ECMP)**
```
cumulus@br-agg-sw-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * fe80::5200:ff:fe1d:3, via swp5.100, weight 1    # dc2-border-1
  * fe80::5200:ff:fe1e:3, via swp6.100, weight 1    # dc2-border-2 (ECMP)
cumulus@br-agg-sw-1:mgmt:~$
```
Plain L3 per tenant VRF, ECMP to both DC2 borders. (On the key-only backbone switches
`sudo` needs the password entered at its prompt, or FRR output comes back empty.)

**Stage 5 - DC2 border (routed to overlay): re-encapsulate into DC2**
```
cumulus@dc2-border-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * 10.2.0.11, via vlan3101_l3 onlink, weight 1
cumulus@dc2-border-1:mgmt:~$
```
The DC2 border puts the routed packet back into an overlay, the DC2 L3VNI `vlan3101_l3`,
toward VTEP `10.2.0.11` (dc2-k8s-leaf-1).

**Stage 6 - DC2 spine (underlay):** routes by the outer VTEP `10.2.0.11`, same as stage 2.

**Stage 7 - DC2 leaf (overlay to L2): deliver to the pod-bearing nodes**
```
cumulus@dc2-k8s-leaf-1:mgmt:~$ sudo vtysh -c "show ip route vrf tenant-k8s 192.168.202.2"
Routing entry for 192.168.202.2/32
  * 10.168.10.22, via vlan210, weight 1
  * 10.168.10.23, via vlan210, weight 1
cumulus@dc2-k8s-leaf-1:mgmt:~$
```
The destination leaf decapsulates and delivers to its local workers `10.168.10.22` / `.23`
on `vlan210` (L2VNI 10210), the nodes running the pods. Note these match the pod placement
from the k8s step (externalTrafficPolicy Local).

### Back up the stack at DC2
The node receives the packet for `192.168.202.2:80`; kube-proxy / Cilium DNATs it to a
backing pod (`10.245.1.47:8080` or `10.245.3.240:8080`) over the Cilium pod overlay; the
app answers; `curl` sees `DC2 demo`.

### The path, one line per hop
`DC1 worker-leaf (L3VNI) -> DC1 spine (underlay) -> DC1 border (overlay to routed) ->
br-agg backbone (routed, ECMP) -> DC2 border (routed to overlay) -> DC2 spine (underlay)
-> DC2 leaf (L3VNI) -> DC2 node -> pod -> app`.

---

## Gotchas
- **Backbone sudo needs the password.** On the key-only br-agg switches, `sudo vtysh`
  returns empty unless you enter the password (over SSH scripting: `echo <pw> | sudo -S ...`).
- **A MAC does not cross DCs.** L2VNI 10120 is only on the worker-leaf pair. Trace a host
  across DCs by its IP (as a service), not its MAC.
- **Two overlays, not one.** A pod-to-pod cross-DC packet is encapsulated by Cilium *and*
  by the fabric. A client-to-VIP packet rides the fabric overlay until the last hop, where
  Cilium delivers to the pod.
- **Dual-homed MACs appear twice in BGP,** once per ES VTEP, both carrying the ESI.
- **Pod placement is dynamic.** The pods reschedule; the VIP's fabric next-hops (stage 7)
  always match wherever the pods currently are, because `externalTrafficPolicy: Local`.
