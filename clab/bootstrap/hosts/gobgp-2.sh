#!/bin/sh
# gobgp-2: two routed /30 uplinks to the backbone (eth1->br-agg-sw-1, eth2->br-agg-sw-2). No bond.
set -e
ip addr show eth1 | grep -q '10.201.20.10' && exit 0
ip link set eth1 up; ip link set eth2 up
ip addr add 10.201.20.10/30 dev eth1
ip addr add 10.201.20.14/30 dev eth2
id user >/dev/null 2>&1 || adduser -D -s /bin/sh user 2>/dev/null; echo "user:Test123" | chpasswd 2>/dev/null; echo "root:Test123" | chpasswd 2>/dev/null; addgroup user wheel 2>/dev/null; pgrep -x dropbear >/dev/null 2>&1 || dropbear -R -p 22 >/dev/null 2>&1
echo "gobgp-2: eth1 10.201.20.10/30 eth2 10.201.20.14/30"
