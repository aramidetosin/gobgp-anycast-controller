# ecloud two-DC lab, replicated in containerlab

A faithful containerlab replica of the ecloud POC: **39 nodes, 75 links**, generated from
the authoritative EVE-NG link map (every p2p link captured live 2026-08-17, none inferred).
Lab lives at `/root/containerlab/ecloud/` on `home-eve` (96 cores, 314GB RAM, bare-metal KVM,
containerlab 0.78.2, vrnetlab checkout at `/root/containerlab/vrnetlab` updated to 2026-07-23).
The whole lab fits with room to spare.

## Research findings that shaped this (verified, not assumed)

1. **Cumulus VX is discontinued.** NVIDIA no longer distributes the image, and containerlab's
   original container-native `cvx` kind is "not maintained/supported". The supported path is
   the **`nvidia_cumulusvx` kind = Cumulus VX running as a QEMU VM inside a Docker container**,
   built with vrnetlab (support merged 2026-06-07). We have the exact image the EVE lab runs
   (`cumulus-linux-5.12.0-vx-amd64-qemu.qcow2`, pulled from EVE-NG), so we are not blocked.
2. **The `nvidia_cumulusvx` kind does NOT apply `startup-config`** (read from source: it only
   mounts `/config` as a persistent overlay and sets creds/hostname on first boot). Configs are
   pushed **post-deploy** by `push_configs.sh` (SSH `nv set` replay), or via NVUE REST :8765.
3. **Palo Alto**: vrnetlab `paloalto_panos` (tested up to PAN-OS 11.2.3). Our PA-VM 12.1.2 is
   newer than the tested list; first boot must be verified, not assumed.
4. **KVM required** on the host (`/dev/kvm`); Cumulus VX under software emulation is unusable.
   home-eve is bare metal with KVM: fine.
5. **Cilium BGP to a containerlab ToR** is a documented pattern (containerlab `k8s-kind` kind /
   Cilium's own BGP dev env), so the k8s side ports cleanly.

## What transfers 1:1, and what does not

| Component | In clab | Notes |
|---|---|---|
| 20 Cumulus switches | `nvidia_cumulusvx` (VM-in-container) | 4GB RAM + 2 vCPU each = 80GB / 40 vCPU |
| 2 PA-VM A/A | `paloalto_panos` (vrnetlab) | 6GB + 2 vCPU each; HA1/2/3 links = plain clab links |
| 16 hosts (k8s, svc, clients, gobgp) | `linux` kind (ubuntu:24.04) | provisioned after deploy |
| shared "internet" segment (EVE net54) | a `bridge` node | clab links are p2p only |
| mgmt network | static `172.29.129.0/24` on the docker mgmt bridge | **an upgrade**: no more DHCP-drifting IPs |
| **All VX quirks** | identical | it is the same 5.12 kernel + switchd: ES bonds need a reboot to converge, `evpn mh redirect-off` per boot, LACP 2-3 min |

**Not a resource saving.** Every switch is still a full VM (same qcow2 as EVE). What you gain
is a declarative, git-versioned, one-command (`clab deploy`) reproducible lab, not efficiency.

## Files
- `ecloud.clab.yml` - the topology (generated; do not hand-edit, edit `build_clab.py`)
- `build_clab.py` - generator holding the authoritative EVE link map + name/interface mapping
- `push_configs.sh` - post-deploy config push (all 20 switches, ordered spines->leaves->borders->backbone)
- `configs/{dc1,dc2,aggr}/` - the switch configs (copy of `fabric-evpn-mh/`, EVPN-MH design)

## Interface / name mapping (from EVE)
- switch ports: `swpN` unchanged. Host NICs: `data0 -> eth1`, `data1 -> eth2`. PAN: `eth1/N -> ethN`.
- EVE names normalised: `DC-2-k8s-svc-1 -> dc2-svc-leaf-1`, `DC-2-k8s-border-1 -> dc2-border-1`,
  `PaloAlto-pri-active-active-1 -> fw-pri`, `GOBGP-controller-1 -> gobgp-1`, `client -> client-1`.

## Deploy sequence
```bash
# 0) images  (or just: ./build_images.sh) (qcow2s already staged on home-eve:/root/containerlab/ecloud/images/)
cd /root/containerlab/vrnetlab      # already checked out; updated to 2026-07-23 (has nvidia/cumulus-vx)
cp /root/containerlab/ecloud/images/cumulus-linux-5.12.0-vx-amd64-qemu.qcow2 nvidia/cumulus-vx/ && (cd nvidia/cumulus-vx && make)
cp /root/containerlab/ecloud/images/PA-VM-KVM-12.1.2.qcow2 paloalto/pan/ && (cd paloalto/pan && make)
#    -> vrnetlab/nvidia_cumulus-vx:5.12.0 , vrnetlab/vr-pan:12.1.2

# 1) deploy the topology (switches take 2-4 min each to become healthy; they boot in parallel)
sudo containerlab deploy -t ecloud.clab.yml

# 2) push the fabric configs (creds cumulus/Clab123!)
./push_configs.sh ./configs
#    then per the VX lesson: reboot each access leaf, wait ~3 min, apply redirect-off + write mem

# 3) provision hosts (not yet scripted): k8s+Cilium on the node containers, BIND on the svc nodes,
#    GoBGP + brain on gobgp-1/2 (nodes/ in this repo), the dc-demo app (demo/), PAN config from
#    fabric-*/firewall/ (interfaces/zones/HA/NAT rulebases).
```

## Known gaps / next steps
- **Host provisioning is not scripted yet** (k8s bootstrap in containers, Cilium BGP peering to the
  leaves, BIND views, GoBGP brain, PAN full config). The switch fabric is the part this delivers.
- PAN 12.1.2 under vrnetlab: verify first boot + interface mapping (`eth1/N -> ethN`).
- Config push filters out mgmt/`REDACTED` snmp lines; review `push_configs.sh` filters before a run.
- Consider `k8s-kind` for the clusters instead of ubuntu containers if a full kubeadm-in-container
  bootstrap proves painful (kind nodes are containers already; Cilium BGP peers to the leaf).
