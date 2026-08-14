#!/usr/bin/env bash
set -u
SP=/private/tmp/claude-501/-Volumes-NVMe-k8s-monitoring-all/eaafd7e3-f419-46bb-9edc-0e1cc6ab84c2/scratchpad
cd "$SP"
# 1) flip client-1 policy static->latency in both node configs
python3 - <<'PY'
import json
for f in ("brain-active.json","brain-standby.json"):
    d=json.load(open(f))
    for m in d["managed"]:
        if m["name"]=="client-1": m["policy"]={"mode":"latency","primary":"DC1"}
    json.dump(d, open(f,"w"), indent=2)
    pol=[m["policy"] for m in d["managed"] if m["name"]=="client-1"][0]
    print(f, "client-1 ->", pol)
PY
python3 -c "import ast; ast.parse(open('controller_brain.py').read()); print('brain.py syntax OK')"
# 2) deploy brain.py + brain.json, restart ONLY gobgp-brain (gobgpd stays up)
for pair in "gobgp-1 brain-active.json" "gobgp-2 brain-standby.json"; do
  n=${pair% *}; bj=${pair#* }
  ssh -o ConnectTimeout=8 "$n" 'sudo tee /opt/gobgp-brain/controller_brain.py >/dev/null' < controller_brain.py
  ssh -o ConnectTimeout=8 "$n" 'sudo tee /etc/gobgp/brain.json >/dev/null' < "$bj"
  ssh -o ConnectTimeout=8 "$n" 'sudo systemctl restart gobgp-brain; sleep 3; echo -n "  brain="; systemctl is-active gobgp-brain'
  echo "$n deployed ($bj)"
done
