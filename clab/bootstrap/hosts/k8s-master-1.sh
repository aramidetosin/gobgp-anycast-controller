#!/bin/sh
# k8s-master-1: first-boot L2/L3 bootstrap = the real host's netplan 01-fabric.yaml, as a script.
# bond0 = 802.3ad fast-LACP, l3+4 hash, MTU 9000, over data0(eth1)+data1(eth2). Idempotent.
set -e
ip link show bond0 >/dev/null 2>&1 && exit 0
ip link add bond0 type bond mode 802.3ad lacp_rate fast miimon 100 xmit_hash_policy layer3+4
ip link set bond0 address 50:00:00:0b:00:01
ip link set eth1 down; ip link set eth2 down
ip link set eth1 master bond0; ip link set eth2 master bond0
ip link set eth1 mtu 9000; ip link set eth2 mtu 9000; ip link set bond0 mtu 9000
ip link set eth1 up; ip link set eth2 up; ip link set bond0 up
ip addr add 10.167.10.11/24 dev bond0
ip route replace default via 10.167.10.1 dev bond0 metric 50
printf 'nameserver 10.167.30.10\nsearch ecloud.lab\n' > /etc/resolv.conf
echo "k8s-master-1: bond0 10.167.10.11/24 via 10.167.10.1 dns 10.167.30.10"
