#!/usr/bin/env python3
import sys, json, os
sys.path.insert(0, "/Users/aramideo/.claude/skills/blog-nugget/scripts")
from excalidraw_helpers import rect, text, ctext, arrow, line, doc

INK="#1e1e1e"; GRAY="#868e96"; BLUE="#1971c2"; ORANGE="#e8590c"; PURPLE="#9c36b5"; DIV="#ced4da"
els=[]

# ---------- titles / plane labels ----------
els.append(text(30, 10, "Per-client steering: control plane vs data plane", 20, INK))
els.append(text(30, 44, "CONTROL PLANE   decides which DC each VIP points at, carries no user traffic", 13, PURPLE))
els.append(line(28, 286, 966, 286, DIV, 2))
els.append(text(30, 296, "DATA PLANE   packets flow client to DC; the controller is never in the path", 13, INK))

# ---------- control plane: the brain ----------
els.append(rect(372, 74, 268, 104, stroke=INK, bg="#ffffff", width=3))
els.append(ctext(506, 88,  "GoBGP controller", 19, INK))
els.append(ctext(506, 116, "brain: picks each VIP's DC", 13, GRAY))
els.append(ctext(506, 136, "monitors DC1 / DC2 health", 12, GRAY))
els.append(ctext(506, 154, "gobgp-1 active / gobgp-2 standby", 12, GRAY))

# steering arrow brain -> aggr
els.append(arrow(516, 178, 576, 360, PURPLE, dash=True, width=3))
els.append(text(590, 196, "set next-hop per VIP:", 13, PURPLE))
els.append(text(590, 216, ".202.3  ->  next-hop .202.1  (DC1)", 12, BLUE))
els.append(text(590, 236, ".202.4  ->  next-hop .202.2  (DC2)", 12, ORANGE))

# ---------- data plane: nodes ----------
els.append(rect(36, 330, 172, 66, stroke=BLUE, bg="#ffffff", width=2))
els.append(ctext(122, 344, "client-1", 16, BLUE))
els.append(ctext(122, 366, "10.80.15.103", 12, GRAY))

els.append(rect(36, 476, 172, 66, stroke=ORANGE, bg="#ffffff", width=2))
els.append(ctext(122, 488, "external-client-2", 14, ORANGE))
els.append(ctext(122, 512, "10.80.15.100", 12, GRAY))

els.append(rect(268, 356, 150, 158, stroke=INK, bg="#ffffff", width=2))
els.append(ctext(343, 372, "PA firewall", 16, INK))
els.append(ctext(343, 394, "A/A pair", 12, GRAY))
els.append(ctext(343, 430, "DNAT +", 12, GRAY))
els.append(ctext(343, 450, "allow rule", 12, GRAY))

els.append(rect(478, 356, 188, 158, stroke=INK, bg="#ffffff", width=2))
els.append(ctext(572, 372, "aggr / backbone", 16, INK))
els.append(ctext(572, 394, "AS 65400", 12, GRAY))
els.append(ctext(572, 432, "recursive", 13, GRAY))
els.append(ctext(572, 452, "next-hop resolve", 13, GRAY))

els.append(rect(726, 300, 226, 86, stroke=BLUE, bg="#ffffff", width=2))
els.append(ctext(839, 312, "DC1", 17, BLUE))
els.append(ctext(839, 336, "dc-demo pods", 12, GRAY))
els.append(ctext(839, 356, "serve 192.168.202.3", 12, BLUE))

els.append(rect(726, 484, 226, 86, stroke=ORANGE, bg="#ffffff", width=2))
els.append(ctext(839, 496, "DC2", 17, ORANGE))
els.append(ctext(839, 520, "dc-demo pods", 12, GRAY))
els.append(ctext(839, 540, "serve 192.168.202.4", 12, ORANGE))

# ---------- data plane: arrows ----------
# clients -> firewall
els.append(arrow(208, 360, 268, 404, BLUE, width=3))
els.append(arrow(208, 508, 268, 466, ORANGE, width=3))
# firewall -> aggr (with DNAT mapping labels)
els.append(arrow(418, 410, 478, 410, BLUE, width=3))
els.append(text(422, 388, ".53 -> 192.168.202.3", 11, BLUE))
els.append(arrow(418, 460, 478, 460, ORANGE, width=3))
els.append(text(422, 464, ".54 -> 192.168.202.4", 11, ORANGE))
# aggr -> DCs (recursive resolution)
els.append(arrow(666, 404, 726, 348, BLUE, width=3))
els.append(text(672, 350, "resolve via .202.1", 11, BLUE))
els.append(arrow(666, 466, 726, 522, ORANGE, width=3))
els.append(text(672, 520, "resolve via .202.2", 11, ORANGE))

# ---------- legend ----------
LY=602
els.append(line(40, LY, 74, LY, BLUE, 4));   els.append(text(80, LY-9, "client-1 path  ->  DC1", 12, BLUE))
els.append(line(250, LY, 284, LY, ORANGE, 4)); els.append(text(290, LY-9, "external-client-2 path  ->  DC2", 12, ORANGE))
els.append(line(560, LY, 594, LY, PURPLE, 3)); els.append(text(600, LY-9, "control plane (brain sets next-hop)", 12, PURPLE))

json.dump(doc(els, bg="#ffffff"), open(os.path.join(os.path.dirname(__file__), "perclient.excalidraw"), "w"))
print("wrote perclient.excalidraw with", len(els), "elements")
