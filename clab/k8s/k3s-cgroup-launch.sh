#!/bin/sh
# Run k3s inside a cgroup-v2 privileged container (the "Kubernetes in Docker on cgroup v2" dance
# that kind/k3d do in their entrypoints). Requires --cgroupns=private (container sees its own
# cgroup root at /sys/fs/cgroup).
set -e
CG=/sys/fs/cgroup

# 1) evacuate EVERY process (incl. this shell and PID 1) out of the cgroup-v2 ROOT into a leaf,
#    so the root has no member procs and can therefore delegate ALL controllers to children.
mkdir -p "$CG/init.scope"
if [ -f "$CG/cgroup.procs" ]; then
  while read -r pid; do
    echo "$pid" > "$CG/init.scope/cgroup.procs" 2>/dev/null || true
  done < "$CG/cgroup.procs"
fi

# 2) delegate every available controller to the subtree
for c in $(cat "$CG/cgroup.controllers"); do
  echo "+$c" > "$CG/cgroup.subtree_control" 2>/dev/null || true
done

# 3) put THIS shell (and thus k3s, its child) into a dedicated leaf so kubelet's
#    /kubepods sibling can be created with controllers.
mkdir -p "$CG/k3s.scope"
echo $$ > "$CG/k3s.scope/cgroup.procs" 2>/dev/null || true

echo "subtree_control=$(cat $CG/cgroup.subtree_control)"
exec /usr/local/bin/k3s "$@"
