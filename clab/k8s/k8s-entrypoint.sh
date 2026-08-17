#!/bin/sh
# ecloud k8s-host entrypoint. Runs as PID 1 in a clab `linux` (privileged, cgroupns=private) node.
# Solves the "Kubernetes in a cgroup-v2 container" problem the way kind/k3d do (evacuate the root
# cgroup as PID 1 so containerd/runc can delegate controllers to the k8s.io child), sets up the
# dual-homed fabric bond from env, then launches k3s by role. All node-specific values come from
# clab `env:`.
set -e
CG=/sys/fs/cgroup
log() { echo "[k8s-entrypoint] $*"; }

# ---- 1) cgroup v2 evacuation (must happen first, as PID 1, before k3s/containerd spawn) ----
if [ -f "$CG/cgroup.controllers" ]; then
  mkdir -p "$CG/init.scope"
  # move THIS process (PID 1) and any stragglers out of the root cgroup
  echo $$ > "$CG/init.scope/cgroup.procs" 2>/dev/null || true
  for pid in $(cat "$CG/cgroup.procs" 2>/dev/null); do
    echo "$pid" > "$CG/init.scope/cgroup.procs" 2>/dev/null || true
  done
  # now the root has no member procs -> delegate every controller to children (incl. k8s.io)
  for c in $(cat "$CG/cgroup.controllers"); do
    echo "+$c" > "$CG/cgroup.subtree_control" 2>/dev/null || true
  done
  log "root procs left=$(wc -l < "$CG/cgroup.procs") subtree_control=[$(cat "$CG/cgroup.subtree_control")]"
fi

# ---- 2) mount propagation + bpffs (Cilium needs these) ----
mount --make-rshared / 2>/dev/null || true
mount -t bpf bpf /sys/fs/bpf 2>/dev/null || true
mount --make-rshared /sys/fs/bpf 2>/dev/null || true
mount -t cgroup2 none /run/cilium/cgroupv2 2>/dev/null || true

# ---- 3) fabric networking: dual-homed bond0 (data0=eth1, data1=eth2), from env ----
if [ -n "$NODE_BOND_IP" ]; then
  log "waiting for fabric interfaces eth1+eth2 ..."
  until ip link show eth1 >/dev/null 2>&1 && ip link show eth2 >/dev/null 2>&1; do sleep 1; done
  if ! ip link show bond0 >/dev/null 2>&1; then
    ip link add bond0 type bond mode 802.3ad lacp_rate fast miimon 100 xmit_hash_policy layer3+4
    [ -n "$NODE_MAC" ] && ip link set bond0 address "$NODE_MAC"
    ip link set eth1 down; ip link set eth2 down
    ip link set eth1 master bond0; ip link set eth2 master bond0
    ip link set eth1 mtu 9000; ip link set eth2 mtu 9000; ip link set bond0 mtu 9000
    ip link set eth1 up; ip link set eth2 up; ip link set bond0 up
    ip addr add "$NODE_BOND_IP" dev bond0
    [ -n "$NODE_GW" ] && ip route replace default via "$NODE_GW" dev bond0 metric 50
    printf 'nameserver %s\nsearch ecloud.lab\n' "${NODE_DNS:-10.167.30.10}" > /etc/resolv.conf
    log "bond0 $NODE_BOND_IP via ${NODE_GW:-none} dns ${NODE_DNS}"
  fi
fi

# ---- 4) launch k3s by role, SUPERVISED (entrypoint stays PID 1 so a k3s crash does not kill the
#         container and lose the clab-wired interfaces). Join roles wait for the server API first. ----
KA="--kubelet-arg=cgroups-per-qos=false --kubelet-arg=enforce-node-allocatable= --kubelet-arg=fail-swap-on=false"
COMMON="--flannel-backend=none --disable-network-policy --disable=traefik --disable=servicelb --disable-kube-proxy --snapshotter=native $KA"
build_cmd() {
  case "$K3S_ROLE" in
    server-init) echo "server --cluster-init --token $K3S_TOKEN --node-ip $NODE_IP --advertise-address $NODE_IP --tls-san $NODE_IP $COMMON" ;;
    server-join) echo "server --server $K3S_URL --token $K3S_TOKEN --node-ip $NODE_IP --advertise-address $NODE_IP $COMMON" ;;
    agent)       echo "agent --server $K3S_URL --token $K3S_TOKEN --node-ip $NODE_IP $KA --snapshotter=native" ;;
    *)           echo "" ;;
  esac
}
CMD=$(build_cmd)
[ -z "$CMD" ] && { log "no K3S_ROLE set; idling"; exec sleep infinity; }
# join roles: wait until the server's API port is reachable, so etcd/agent join does not fail fatally
if [ "$K3S_ROLE" != "server-init" ] && [ -n "$K3S_URL" ]; then
  host=$(echo "$K3S_URL" | sed -E 's#https?://([^:/]+).*#\1#')
  log "waiting for server API $host:6443 ..."
  until nc -z -w3 "$host" 6443 2>/dev/null; do sleep 5; done
  log "server API reachable"
fi
log "supervising k3s role=$K3S_ROLE node-ip=$NODE_IP"
while true; do
  /usr/local/bin/k3s $CMD
  log "k3s exited ($?), restarting in 10s (container + interfaces stay up)"
  sleep 10
done
