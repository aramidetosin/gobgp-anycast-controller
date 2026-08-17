#!/usr/bin/env python3
"""Host + firewall first-boot provisioning for the ecloud containerlab.
Source of truth: the REAL hosts' netplan (bond0 802.3ad fast-LACP l3+4 MTU9000, static IP,
default via the anycast .1 gateway, lab DNS) captured from the eve-office host backups
2026-08-16, plus live client/gobgp addressing. Nothing invented.

Emits: bootstrap/hosts/<name>.sh  (run by clab `exec` at container start)
Used by build_clab.py to render the linux / k8s-kind / ext-container / paloalto nodes."""

# name: (bond_ip/len, gw, dns, mac, dc)   -- dual-homed hosts (data0=eth1, data1=eth2)
BONDED = {
 "k8s-master-1":     ("10.167.10.11/24","10.167.10.1","10.167.30.10","50:00:00:0b:00:01","dc1"),
 "k8s-master-2":     ("10.167.10.12/24","10.167.10.1","10.167.30.10","50:00:00:0c:00:01","dc1"),
 "k8s-master-3":     ("10.167.10.13/24","10.167.10.1","10.167.30.10","50:00:00:0d:00:01","dc1"),
 "k8s-woker-1":      ("10.167.20.11/24","10.167.20.1","10.167.30.10","50:00:00:0e:00:01","dc1"),
 "k8s-woker-2":      ("10.167.20.12/24","10.167.20.1","10.167.30.10","50:00:00:0f:00:01","dc1"),
 "k8s-woker-3":      ("10.167.20.13/24","10.167.20.1","10.167.30.10","50:00:00:10:00:01","dc1"),
 "service-node-dns-ntp": ("10.167.30.10/24","10.167.30.1","127.0.0.1","50:00:00:11:00:01","dc1"),
 "dc2-k8s-master-1": ("10.168.10.11/24","10.168.10.1","10.168.30.10","50:00:00:1f:00:01","dc2"),
 "dc2-k8s-worker-1": ("10.168.10.21/24","10.168.10.1","10.168.30.10","50:00:00:20:00:01","dc2"),
 "dc2-k8s-worker-2": ("10.168.10.22/24","10.168.10.1","10.168.30.10","50:00:00:21:00:01","dc2"),
 "dc2-k8s-worker-3": ("10.168.10.23/24","10.168.10.1","10.168.30.10","50:00:00:22:00:01","dc2"),
 "dc2-svc-ntp-dns":  ("10.168.30.10/24","10.168.30.1","127.0.0.1","50:00:00:23:00:01","dc2"),
}
# single-NIC hosts on the 'internet' bridge (eth1)
CLIENTS = {
 "client-1":          ("10.80.15.103/24","10.80.15.1"),
 "external-client-2": ("10.80.15.100/24","10.80.15.1"),
}
# gobgp controllers: two ROUTED /30s to the two aggr switches (eth1->aggr-1, eth2->aggr-2), no bond
GOBGP = {
 "gobgp-1": ("10.201.20.2/30","10.201.20.6/30"),
 "gobgp-2": ("10.201.20.10/30","10.201.20.14/30"),
}
# k8s cluster membership (cluster -> nodes; first control-plane is the cluster-init server)
CLUSTERS = {
 "dc1": {"control-plane":["k8s-master-1","k8s-master-2","k8s-master-3"], "workers":["k8s-woker-1","k8s-woker-2","k8s-woker-3"], "pod":"10.244.0.0/16","svc":"10.96.0.0/12","asn":65010},
 "dc2": {"control-plane":["dc2-k8s-master-1"], "workers":["dc2-k8s-worker-1","dc2-k8s-worker-2","dc2-k8s-worker-3"], "pod":"10.245.0.0/16","svc":"10.96.0.0/16","asn":65020},
}
K8S_TOKENS = {"dc1": "ecloud-dc1-k3s-9f3a2c", "dc2": "ecloud-dc2-k3s-7b2e11"}
# derived: k8s node -> role
K8S_NODES = {}
for _dc, _c in CLUSTERS.items():
    for _i, _n in enumerate(_c["control-plane"]):
        K8S_NODES[_n] = {"dc": _dc, "role": "server-init" if _i == 0 else "server-join"}
    for _n in _c["workers"]:
        K8S_NODES[_n] = {"dc": _dc, "role": "agent"}

def first_master_ip(dc):
    return BONDED[CLUSTERS[dc]["control-plane"][0]][0].split("/")[0]

def k8s_env(name):
    """env passed to the ecloud-k8s-host container for a k8s node (drives its PID-1 entrypoint)."""
    info = K8S_NODES[name]; dc = info["dc"]
    ip, gw, dns, mac, _ = BONDED[name]
    env = {"K3S_ROLE": info["role"], "K3S_TOKEN": K8S_TOKENS[dc],
           "NODE_IP": ip.split("/")[0], "NODE_BOND_IP": ip, "NODE_GW": gw, "NODE_DNS": dns, "NODE_MAC": mac}
    if info["role"] != "server-init":
        env["K3S_URL"] = f"https://{first_master_ip(dc)}:6443"
    return env

def bond_script(name, ip, gw, dns, mac):
    return f"""#!/bin/sh
# {name}: first-boot L2/L3 bootstrap = the real host's netplan 01-fabric.yaml, as a script.
# bond0 = 802.3ad fast-LACP, l3+4 hash, MTU 9000, over data0(eth1)+data1(eth2). Idempotent.
set -e
ip link show bond0 >/dev/null 2>&1 && exit 0
ip link add bond0 type bond mode 802.3ad lacp_rate fast miimon 100 xmit_hash_policy layer3+4
ip link set bond0 address {mac}
ip link set eth1 down; ip link set eth2 down
ip link set eth1 master bond0; ip link set eth2 master bond0
ip link set eth1 mtu 9000; ip link set eth2 mtu 9000; ip link set bond0 mtu 9000
ip link set eth1 up; ip link set eth2 up; ip link set bond0 up
ip addr add {ip} dev bond0
# drop docker's metric-0 eth0 (mgmt) default so the FABRIC (bond0) is the sole default; else
# cross-subnet replies leave via mgmt and die. mgmt /24 stays (directly connected) for host SSH.
while ip route show default dev eth0 2>/dev/null | grep -q .; do ip route del default dev eth0 2>/dev/null || break; done
ip route replace default via {gw} dev bond0
printf 'nameserver {dns}\\nsearch ecloud.lab\\n' > /etc/resolv.conf
echo "{name}: bond0 {ip} via {gw} dns {dns}"
"""

def client_script(name, ip, gw):
    return f"""#!/bin/sh
# {name}: single data NIC on the 'internet' segment (10.80.15.0/24), lab DNS via the FW-published VIP.
set -e
ip addr show eth1 | grep -q '{ip.split('/')[0]}' && exit 0
ip link set eth1 up
ip addr add {ip} dev eth1
while ip route show default dev eth0 2>/dev/null | grep -q .; do ip route del default dev eth0 2>/dev/null || break; done
ip route replace default via {gw} dev eth1
printf 'nameserver 10.80.15.41\\nsearch ecloud.lab\\n' > /etc/resolv.conf
echo "{name}: eth1 {ip} via {gw}"
"""

def gobgp_script(name, ip1, ip2):
    return f"""#!/bin/sh
# {name}: two routed /30 uplinks to the backbone (eth1->br-agg-sw-1, eth2->br-agg-sw-2). No bond.
set -e
ip addr show eth1 | grep -q '{ip1.split('/')[0]}' && exit 0
ip link set eth1 up; ip link set eth2 up
ip addr add {ip1} dev eth1
ip addr add {ip2} dev eth2
echo "{name}: eth1 {ip1} eth2 {ip2}"
"""

if __name__ == "__main__":
    import os, sys
    out = sys.argv[1] if len(sys.argv) > 1 else "bootstrap/hosts"
    os.makedirs(out, exist_ok=True)
    for n,(ip,gw,dns,mac,dc) in BONDED.items():
        open(f"{out}/{n}.sh","w").write(bond_script(n,ip,gw,dns,mac))
    for n,(ip,gw) in CLIENTS.items():
        open(f"{out}/{n}.sh","w").write(client_script(n,ip,gw))
    for n,(a,b) in GOBGP.items():
        open(f"{out}/{n}.sh","w").write(gobgp_script(n,a,b))
    for f in os.listdir(out): os.chmod(f"{out}/{f}", 0o755)
    print(f"wrote {len(os.listdir(out))} host bootstrap scripts to {out}/")
