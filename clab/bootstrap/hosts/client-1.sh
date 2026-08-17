#!/bin/sh
# client-1: single data NIC on the 'internet' segment (10.80.15.0/24), lab DNS via the FW-published VIP.
set -e
ip addr show eth1 | grep -q '10.80.15.103' && exit 0
ip link set eth1 up
ip addr add 10.80.15.103/24 dev eth1
while ip route show default dev eth0 2>/dev/null | grep -q .; do ip route del default dev eth0 2>/dev/null || break; done
ip route replace default via 10.80.15.1 dev eth1
printf 'nameserver 10.80.15.41\nsearch ecloud.lab\n' > /etc/resolv.conf
echo "client-1: eth1 10.80.15.103/24 via 10.80.15.1"
