#!/bin/sh
# external-client-2: single data NIC on the 'internet' segment (10.80.15.0/24), lab DNS via the FW-published VIP.
set -e
ip addr show eth1 | grep -q '10.80.15.100' && exit 0
ip link set eth1 up
ip addr add 10.80.15.100/24 dev eth1
ip route replace default via 10.80.15.1 dev eth1 metric 50
printf 'nameserver 10.80.15.41\nsearch ecloud.lab\n' > /etc/resolv.conf
echo "external-client-2: eth1 10.80.15.100/24 via 10.80.15.1"
