#!/usr/bin/env python3
"""Patch vrnetlab nvidia/cumulus-vx/docker/launch.py to APPLY /config/startup-config.cfg
at first boot (the way vJunos/cEOS kinds do). containerlab's VRNode already delivers the
node's `startup-config:` file to <labdir>/<node>/config/startup-config.cfg (bind-mounted at
/config); the stock Cumulus launcher just never consumed it. This adds that step:
after switchd is active, SSH into the VM (127.0.0.1, known creds), replay the nv-set
lines, `nv config apply -y`, then mark running. Idempotent: skipped if no file, and a
marker prevents re-apply on overlay reuse."""
import sys, re
p = sys.argv[1]
t = open(p).read()

HOOK = """        if self._bootstrap_done:
            if not self._switchd_is_ready():
                self.spins += 1
                return
            self.running = True"""
NEW = """        if self._bootstrap_done:
            if not self._switchd_is_ready():
                self.spins += 1
                return
            # ecloud: apply the containerlab startup-config (if provided) BEFORE declaring running
            try:
                self._apply_startup_config()
            except Exception as exc:  # never block boot on a config problem; log it
                self.logger.error("startup-config apply failed: %s", exc)
            self.running = True"""
assert HOOK in t, "hook point not found - launcher changed upstream?"
t = t.replace(HOOK, NEW, 1)

METHOD = '''
    # ── ecloud addition: startup-config apply over SSH ─────────────────────────
    STARTUP_CONFIG_FILE = "/config/startup-config.cfg"
    STARTUP_APPLIED_MARK = "/config/.startup-config.applied"

    def _apply_startup_config(self):
        import os, subprocess
        if not os.path.exists(self.STARTUP_CONFIG_FILE):
            self.logger.info("No startup-config at %s, skipping", self.STARTUP_CONFIG_FILE)
            return
        if os.path.exists(self.STARTUP_APPLIED_MARK):
            self.logger.info("startup-config already applied (marker present), skipping")
            return
        self.logger.info("Applying startup-config %s over SSH ...", self.STARTUP_CONFIG_FILE)
        ssh = ["sshpass", "-p", "Clab123!", "ssh",
               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", "cumulus@127.0.0.1"]
        # 1) wait for the VM's sshd (switchd is up, but sshd may lag a few seconds)
        for i in range(30):
            r = subprocess.run(ssh + ["true"], capture_output=True, timeout=20)
            if r.returncode == 0:
                break
            import time as _t; _t.sleep(3)
        else:
            raise RuntimeError("VM sshd not reachable for startup-config apply")
        # 2) replay the nv-set lines (file is plain lines; skip blanks/comments)
        with open(self.STARTUP_CONFIG_FILE) as f:
            body = "".join(l for l in f if l.strip() and not l.lstrip().startswith("#"))
        r = subprocess.run(ssh + ["bash -s"], input=body.encode(), capture_output=True, timeout=600)
        self.logger.info("nv-set replay rc=%s stderr=%s", r.returncode, r.stderr.decode()[-300:])
        # 3) apply
        r = subprocess.run(ssh + ["nv config apply -y 2>&1 | tail -3"], capture_output=True, timeout=600)
        out = r.stdout.decode()
        self.logger.info("nv config apply: %s", out.strip()[-300:])
        if "applied" in out:
            open(self.STARTUP_APPLIED_MARK, "w").write("ok\\n")
            self.logger.info("startup-config applied and marked")
        else:
            self.logger.warning("nv config apply did not report 'applied': %s", out[-200:])

    def bootstrap_spin(self):'''
assert "\n    def bootstrap_spin(self):" in t
t = t.replace("\n    def bootstrap_spin(self):", METHOD, 1)
open(p, "w").write(t)
print("patched:", p)
