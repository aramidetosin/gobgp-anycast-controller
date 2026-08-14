#!/usr/bin/env bash
set -u
NODE="$1"
SP=/private/tmp/claude-501/-Volumes-NVMe-k8s-monitoring-all/eaafd7e3-f419-46bb-9edc-0e1cc6ab84c2/scratchpad
if [ "$NODE" = gobgp-1 ]; then BJ="$SP/brain-active.json"; else BJ="$SP/brain-standby.json"; fi

echo "== $NODE: 1) gobgpd.conf export set =="
ssh -o ConnectTimeout=8 "$NODE" 'sudo python3 -' < "$SP/gobgpd_edit.py"

echo "== $NODE: 2) deploy brain.py =="
ssh -o ConnectTimeout=8 "$NODE" 'sudo tee /opt/gobgp-brain/controller_brain.py >/dev/null && echo written' < "$SP/controller_brain.py"

echo "== $NODE: 3) deploy brain.json =="
ssh -o ConnectTimeout=8 "$NODE" 'sudo tee /etc/gobgp/brain.json >/dev/null && echo written' < "$BJ"

echo "== $NODE: 4) restart gobgpd then gobgp-brain =="
ssh -o ConnectTimeout=8 "$NODE" 'sudo systemctl restart gobgpd; sleep 3; sudo systemctl restart gobgp-brain; sleep 4; echo -n "gobgpd="; systemctl is-active gobgpd; echo -n "brain="; systemctl is-active gobgp-brain'

echo "== $NODE: 5) RIB (originated .202.x) + brain status =="
ssh -o ConnectTimeout=8 "$NODE" 'echo "[rib .202.x]"; gobgp global rib 2>/dev/null | grep -E "192.168.202.[034]/32" ; echo "[brain]"; cat /run/gobgp-brain-status.json | python3 -m json.tool 2>/dev/null | grep -E "\"role\"|\"name\"|\"target\"|\"reason\"|\"prefix\"|DC1.*ok|DC2.*ok|\"ok\"" | head -30'
