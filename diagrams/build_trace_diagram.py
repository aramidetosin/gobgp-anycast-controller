#!/usr/bin/env python3
import sys, json, os
sys.path.insert(0, "/Users/aramideo/.claude/skills/blog-nugget/scripts")
from excalidraw_helpers import rect, text, ctext, arrow, line, doc

INK="#1e1e1e"; GRAY="#868e96"; BLUE="#1971c2"; ORANGE="#e8590c"
GREEN="#2f9e44"; PURPLE="#9c36b5"; DIV="#ced4da"
SB="#eef4fc"; SO="#fdf0e6"; SG="#eaf6ee"; SP="#f5ecfa"
els=[]

# ================= title =================
els.append(text(28, 12, "Tracing across the two-DC fabric: a service (L1 to L7) and a MAC (L1 to L2)", 21, INK))
els.append(text(28, 44, "The pod network (Cilium VXLAN) rides on the data-center fabric (Cumulus EVPN VXLAN), which rides on a plain routed underlay. Redundancy at every hop: dual-homing + ECMP.", 12.5, GRAY))

# ================= TRACE B: service, L1-L7, DC1 -> DC2 =================
els.append(text(28, 84, "TRACE B   service 192.168.202.2 (DC2 dc-demo VIP), followed DC1 to DC2   ·   command at each hop:  vtysh -c \"show ip route vrf tenant-k8s 192.168.202.2\"", 13, INK))

# band labels
els.append(text(60,  120, "DC1  ·  VXLAN overlay on routed underlay", 12, BLUE))
els.append(text(560, 120, "BACKBONE  ·  routed L3", 12, GREEN))
els.append(text(890, 120, "DC2  ·  VXLAN overlay + underlay", 12, ORANGE))
els.append(text(1300,120, "KUBERNETES", 12, PURPLE))

service = [
 dict(n="DC1 leaf",   r="into L3VNI",     t="L3",         c=BLUE,   bg=SB, d=["worker-leaf","VTEP 10.0.0.13","VXLAN encap"]),
 dict(n="DC1 spine",  r="underlay",       t="underlay",   c=BLUE,   bg=SB, d=["routes by","outer VTEP","out swp7"]),
 dict(n="DC1 border", r="decap to route", t="overlay>L3", c=BLUE,   bg=SB, d=["decap VXLAN","out swp3.100","to backbone"]),
 dict(n="br-agg",     r="routed backbone",t="L3 routed",  c=GREEN,  bg=SG, d=["tenant-k8s VRF","ECMP","swp5/6.100"]),
 dict(n="DC2 border", r="route to encap", t="L3>overlay", c=ORANGE, bg=SO, d=["into vlan3101_l3","VTEP 10.2.0.11","re-encap"]),
 dict(n="DC2 spine",  r="underlay",       t="underlay",   c=ORANGE, bg=SO, d=["routes by","outer VTEP"]),
 dict(n="DC2 leaf",   r="decap to node",  t="overlay>L2", c=ORANGE, bg=SO, d=["to workers","10.168.10.21/.22","vlan210 (L2VNI 10210)"]),
 dict(n="DC2 node",   r="pod, then app",  t="L4 to L7",   c=PURPLE, bg=SP, d=["pod 10.245.x","TCP :8080","HTTP: 'DC2 demo'"]),
]
x0, step, w, by, bh = 25, 178, 152, 200, 82
for i,s in enumerate(service):
    x = x0 + i*step; cx = x + w/2
    els.append(ctext(cx, 176, s["t"], 11, s["c"]))                 # layer tag above
    els.append(rect(x, by, w, bh, stroke=s["c"], bg=s["bg"], width=2))
    els.append(ctext(cx, by+12, s["n"], 15, INK))
    els.append(ctext(cx, by+40, s["r"], 12, s["c"]))
    for j,ln in enumerate(s["d"]):
        els.append(ctext(cx, by+bh+8+j*17, ln, 11, GRAY))
    if i < len(service)-1:
        els.append(arrow(x+w, by+bh/2, x+step, by+bh/2, s["c"], width=3))

# ================= TRACE A: MAC, L1-L2, within DC1 =================
ay = 470
els.append(line(28, ay-24, 1424, ay-24, DIV, 2))
els.append(text(28, ay-14, "TRACE A   MAC 50:00:00:0e:00:01 (DC1 k8s-worker-1), within DC1   ·   command:  vtysh -c \"show evpn mac vni 10120 mac <MAC>\"", 13, INK))

# node
els.append(rect(40, ay+40, 220, 92, stroke=PURPLE, bg=SP, width=2))
els.append(ctext(150, ay+52, "k8s-worker-1 (node)", 14, INK))
els.append(ctext(150, ay+74, "host 10.167.20.11", 12, GRAY))
els.append(ctext(150, ay+94, "MAC 50:00:00:0e:00:01", 11, PURPLE))
els.append(ctext(150, ay+112,"L1: swp3  ·  L2: bond1", 11, GRAY))

# dual-homed ES to two leaves
els.append(rect(470, ay+8,  240, 72, stroke=BLUE, bg=SB, width=2))
els.append(ctext(590, ay+20, "worker-leaf-1", 14, INK))
els.append(ctext(590, ay+42, "VTEP 10.0.0.13  ·  bond1", 12, GRAY))
els.append(ctext(590, ay+60, "MAC = local", 11, BLUE))
els.append(rect(470, ay+96, 240, 72, stroke=BLUE, bg=SB, width=2))
els.append(ctext(590, ay+108,"worker-leaf-2", 14, INK))
els.append(ctext(590, ay+130,"VTEP 10.0.0.14  ·  bond1", 12, GRAY))
els.append(ctext(590, ay+148,"MAC = local (same ESI)", 11, BLUE))

els.append(arrow(260, ay+72, 470, ay+46, INK, width=3))
els.append(arrow(260, ay+98, 470, ay+130, INK, width=3))
els.append(text(276, ay+34, "LACP bond1  ·  Ethernet Segment", 12, INK))
els.append(text(276, ay+52, "ESI 03:44:38:39:be:ef:12:00:00:01", 11, GRAY))

# EVPN result
els.append(rect(770, ay+40, 300, 120, stroke=GREEN, bg=SG, width=2))
els.append(ctext(920, ay+52, "L2VNI 10120  (VLAN 120)", 14, INK))
els.append(ctext(920, ay+76, "EVPN Type-2 (MAC/IP)", 12, GREEN))
els.append(ctext(920, ay+96, "advertised from BOTH VTEPs", 12, GRAY))
els.append(ctext(920, ay+114,"10.0.0.13 and 10.0.0.14, each", 11, GRAY))
els.append(ctext(920, ay+130,"stamped with the ESI = dual-homed,", 11, GRAY))
els.append(ctext(920, ay+146,"no peerlink. Stays inside DC1.", 11, GRAY))
els.append(arrow(710, ay+72, 770, ay+90, GREEN, width=3))
els.append(arrow(710, ay+132,770, ay+110, GREEN, width=3))

# ================= layer legend =================
els.append(rect(1104, ay+8, 356, 190, stroke=GRAY, bg="#ffffff", width=2))
els.append(text(1120, ay+16, "Layer map in this fabric", 13, INK))
leg = [
 ("L1 physical", "leaf swp <-> node NIC", INK),
 ("L2 data link", "LACP bond = Ethernet Segment", INK),
 ("L2 overlay", "fabric VXLAN L2VNI, EVPN Type-2", BLUE),
 ("L3 overlay", "L3VNI symmetric IRB, EVPN Type-5", ORANGE),
 ("L3 underlay", "loopback VTEPs, spine BGP", GREEN),
 ("pod overlay", "Cilium VXLAN pods + BGP VIP", PURPLE),
 ("L4-L7", "TCP :80 to the VIP, HTTP app", INK),
]
for k,(a,b,cc) in enumerate(leg):
    yy = ay+40+k*22
    els.append(text(1126, yy, a, 11.5, cc))
    els.append(text(1236, yy, b, 10.5, GRAY))

out = os.path.join(os.path.dirname(__file__), "trace-l1-l7.excalidraw")
json.dump(doc(els, bg="#ffffff"), open(out,"w"))
print("wrote", out, "with", len(els), "elements")
