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

# 1) deploy: EVERYTHING bootstraps from first boot. Nothing to push.
sudo containerlab deploy -t ecloud.clab.yml
#    switches healthy + BGP/EVPN converged ~4 min, host LACP formed ~6 min, PANs configured ~20 min
```
(`push_configs.sh` remains as a manual fallback only.)

## First-boot bootstrap (how every node self-configures on `clab deploy`)

| Node type | Mechanism | Time to ready |
|---|---|---|
| 20 Cumulus switches | `startup-config: bootstrap/<name>.cfg` (filtered nv-set) applied over SSH by the patched launcher once switchd is up; EVPN-MH leaves then self-reboot + `evpn mh redirect-off` | ~2 min spines/borders, ~4 min ES leaves, LACP formed ~6 min |
| 16 hosts | `linux` kind + `exec: sh /bootstrap/<name>.sh` (bind-mounted): bond0 802.3ad fast-LACP l3+4 MTU9000 over eth1/eth2 = the real netplan; clients on the `internet` bridge; gobgp routed /30s | seconds |
| 2 Palo Altos | `startup-config: bootstrap/fw-*.xml` = the FULL live `<config>` (46KB) loaded via the API by the patched launcher: phase 1 set jumbo-frame + commit + QEMU reset; phase 2 wait auto-commit, load config, commit | ~20 min (PAN-OS 12 boots twice) |

Verified hands-off from a cold deploy: 20/20 switches healthy + BGP/EVPN converged, all ES bonds up,
host LACP partner = the leaf pair's ESI system-mac (`44:38:39:be:ef:1x`), hosts ping their anycast gateway.

### Launcher patches (re-applied from pristine by the `*.patch.py`; resulting launchers kept as `*.patched`)
- `cvx_startup_config.patch.py` (Cumulus): (1) apply startup-config over SSH; (2) fix an UPSTREAM bug
  where `_switchd_is_ready` compared a str regex match to `b"active"`, so no VX node ever became
  running/healthy; (3) EVPN-MH: reboot once after config, then `redirect-off` + write mem; (4)
  overlay-reuse login (stock sends the factory password on a reused overlay: "Login incorrect", wedged).
- `pan_panos12.patch.py` (PAN-OS 12): (1) nudge newlines after the forced password change (no prompt is
  emitted otherwise); (2) liberal `admin@[^>]*>` prompt; (3) send `show jobs processed` BEFORE the
  blocking expect (the scrapli console read blocks up to 3600s on a silent console); (4) XML
  startup-config via API import / load config from / commit; (5) jumbo-frame needs a REBOOT to become
  active: phase 1 + QEMU `system_reset` (the API restart is a no-op on PAN-OS 12) + real down/up
  detection + wait for the post-reboot auto-commit before committing.
- Per-node env on the PAN kind: `QEMU_CPU: host` (clab hard-codes qemu64; PAN-OS 12 needs x86-64-v2),
  `QEMU_SMP: 8`, `QEMU_MEMORY: 16384` (vrnetlab's 2 vCPU / 6 GB drops PAN-OS 12 into the Maintenance
  Recovery Tool; the working EVE PANs run 8 / 16 GB).
- Why XML for the PAN: replaying the set-format export line by line fails silently (lines are validated
  as entered and the export is not in dependency order; the launcher still says "Startup complete" but
  the running config is empty). `pan_xml_extract.py` builds the loadable XML from op-mode
  `show config running` (NOT `configure; show`, which splits into per-section envelopes and omits vsys).

### Operational gotchas
- Recreating ONE node makes clab recreate its link-peers (no hot-plug on VM kinds). Safe now thanks to
  the overlay-reuse fix, but expect neighbours to reboot.
- A node's `cumulus_overlay.qcow2` persists in the labdir across `docker rm`; `rm -rf clab-ecloud` for a
  true clean slate.
- Host prerequisites (all persisted): `bonding` module, the `internet` bridge (`clab-internet-bridge.service`),
  container egress NAT (`clab-container-nat.service`, because docker runs `iptables:false` on this host).
- Reach the VMs via `docker exec <c> sshpass -p Clab123! ssh cumulus@127.0.0.1` (Cumulus) or the XML API
  from inside the container with `uv run --no-project python3` (PAN). Mgmt IPs are not routable from the
  host shell.

## Known gaps / next steps
- **Kubernetes itself is not yet stood up on the host containers** (kind/kubeadm + Cilium BGP + LB pools
  + dc-demo), nor BIND on the svc nodes, nor the GoBGP brain. Hosts have their L2/L3 identity from first
  boot; the k8s control plane is the next layer.
- Consider `k8s-kind` for the clusters instead of ubuntu containers if a full kubeadm-in-container
  bootstrap proves painful (kind nodes are containers already; Cilium BGP peers to the leaf).
