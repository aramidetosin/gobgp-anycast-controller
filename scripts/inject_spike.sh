#!/usr/bin/env bash
set -u
IFACE=ens4; VIP=192.168.202.1; NODES="gobgp-1 gobgp-2"
teardown() { for n in $NODES; do ssh -o ConnectTimeout=6 "$n" "sudo tc qdisc del dev $IFACE root 2>/dev/null" 2>/dev/null; done; }
trap teardown EXIT

setup() { # $1 = netem delay; only dst .202.1 is delayed, everything else (BGP, .202.2) rides the default class
  for n in $NODES; do
    ssh -o ConnectTimeout=8 "$n" "sudo tc qdisc del dev $IFACE root 2>/dev/null; \
      sudo tc qdisc add dev $IFACE root handle 1: htb default 10 && \
      sudo tc class add dev $IFACE parent 1: classid 1:10 htb rate 1000mbit && \
      sudo tc class add dev $IFACE parent 1: classid 1:20 htb rate 1000mbit && \
      sudo tc qdisc add dev $IFACE parent 1:20 handle 20: netem delay $1 && \
      sudo tc filter add dev $IFACE parent 1: protocol ip prio 1 u32 match ip dst $VIP/32 flowid 1:20 && echo applied" 2>/dev/null
  done
}
change() { for n in $NODES; do ssh -o ConnectTimeout=6 "$n" "sudo tc qdisc change dev $IFACE parent 1:20 handle 20: netem delay $1" 2>/dev/null; done; }
snap() {
  ssh -o ConnectTimeout=6 gobgp-1 'cat /run/gobgp-brain-status.json' 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin); h=d["health"]
c=[v for v in d["vips"] if v["name"]=="client-1"][0]
print("    client-1 -> %-3s (%-16s) smoothed DC1 %.2f / DC2 %.2f  lead(DC2) %+.2fms  [raw DC1 %.1f]"%(
  c["target"],c["reason"],h["DC1"]["rtt_ms"],h["DC2"]["rtt_ms"],h["DC1"]["rtt_ms"]-h["DC2"]["rtt_ms"],h["DC1"].get("raw_ms",0)))'
}
e2e() { ssh -o ConnectTimeout=6 user@172.29.129.89 'curl -s -m4 http://10.80.15.53/ | grep -oiE "DC[12] demo"' 2>/dev/null; }

echo ">>> PHASE 1: moderate DC1 spike (netem 0.5ms) -> expect HOLD DC1 (lead stays < 1.5)"
setup 0.5ms >/dev/null
for i in $(seq 1 8); do sleep 2; snap; done
echo "    .53 end-to-end: $(e2e)"

echo; echo ">>> PHASE 2: large DC1 spike (netem 2ms) -> expect MOVE to DC2 (lead > 1.5)"
change 2ms
for i in $(seq 1 8); do sleep 2; snap; done
echo "    .53 end-to-end: $(e2e)"

echo; echo ">>> remove spike -> expect RETURN to DC1"
teardown
for i in $(seq 1 7); do sleep 2; snap; done
echo "    .53 end-to-end: $(e2e)"
echo; echo "=== iBGP sessions still up after the test? ==="
ssh -o ConnectTimeout=6 gobgp-1 'gobgp neighbor 2>/dev/null | grep -E "10.201.20|Establ" | head' 2>/dev/null
