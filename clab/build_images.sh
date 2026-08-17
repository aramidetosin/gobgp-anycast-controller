#!/usr/bin/env bash
# Build the two vrnetlab images from the qcow2s in ./images (run once, ~10-15 min).
set -e
VR=/root/containerlab/vrnetlab
IMG=/root/containerlab/ecloud/images
echo "=== Cumulus VX 5.12.0 -> vrnetlab/nvidia_cumulus-vx:5.12.0 ==="
cp -n "$IMG/cumulus-linux-5.12.0-vx-amd64-qemu.qcow2" "$VR/nvidia/cumulus-vx/"
( cd "$VR/nvidia/cumulus-vx" && make )
echo "=== PA-VM 12.1.2 -> vrnetlab/vr-pan (or paloalto_pa-vm):12.1.2 ==="
cp -n "$IMG/PA-VM-KVM-12.1.2.qcow2" "$VR/paloalto/pan/"
( cd "$VR/paloalto/pan" && make )
echo "=== GoBGP binaries -> bootstrap/hosts/gobgp/ (airgap: baked into the controller nodes' bind mount) ==="
GB=/root/containerlab/ecloud/bootstrap/hosts/gobgp
GBVER=3.30.0
if [ ! -x "$GB/gobgpd" ]; then
  mkdir -p "$GB"
  curl -fsSL -o /tmp/gobgp.tgz "https://github.com/osrg/gobgp/releases/download/v${GBVER}/gobgp_${GBVER}_linux_amd64.tar.gz"
  tar xzf /tmp/gobgp.tgz -C "$GB" gobgp gobgpd && chmod +x "$GB/gobgp" "$GB/gobgpd"
fi
echo "=== result ==="
docker images | grep -iE "cumulus|pa-vm|vr-pan"
ls -la "$GB"/gobgp "$GB"/gobgpd 2>/dev/null
