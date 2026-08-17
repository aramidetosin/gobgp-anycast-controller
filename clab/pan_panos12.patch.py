#!/usr/bin/env python3
"""Patch vrnetlab paloalto/pan/docker/launch.py for PAN-OS 12.x first boot.
Verified against a live PA-VM 12.1.2 boot: after the forced admin password change
("Password changed"), PAN-OS 12 does NOT emit the CLI prompt on the serial console until it
receives a keystroke, so the stock launcher (which only acts on a matched prompt) deadlocks
waiting for 'admin@PA-VM>' forever (10+ min observed). Fix: after confirming the new password,
send a newline to elicit the prompt; also match the prompt liberally (admin@<anything>>) so a
pre-set hostname / HA-state prompt like 'admin@fw-pri(active-primary)>' is accepted too."""
import sys
p = sys.argv[1]
t = open(p).read()

OLD_PAT = '                b"admin@PA-VM>",'
NEW_PAT = '                b"admin@[^>\\r\\n]*>",   # ecloud fix: PAN-OS 12 / hostname / HA-state prompts'
assert OLD_PAT in t, "prompt pattern anchor not found"
t = t.replace(OLD_PAT, NEW_PAT, 1)

OLD_CONFIRM = '''            elif ridx == 7:
                self.logger.debug(f"confirming 'new' password '{self.password}'")
                self.wait_write(self.password, wait=None)'''
NEW_CONFIRM = '''            elif ridx == 7:
                self.logger.debug(f"confirming 'new' password '{self.password}'")
                self.wait_write(self.password, wait=None)
                # ecloud fix: PAN-OS 12 shows no prompt after "Password changed" until a keystroke
                # arrives; nudge the console so the admin@...> prompt is emitted and matched next spin.
                time.sleep(3)
                self.wait_write("", wait=None)
                time.sleep(2)
                self.wait_write("", wait=None)'''
assert OLD_CONFIRM in t, "confirm-password anchor not found"
t = t.replace(OLD_CONFIRM, NEW_CONFIRM, 1)

# Fix 3: the auto-commit wait loop only sends "show jobs processed" when the console buffer is
# EMPTY (res == b""). PAN-OS 12 keeps echoing prompt/command fragments on the serial console
# ("admin@PA-VM> set admin@PA-VM> set cli ..."), so the buffer is never empty, the kick is never
# sent, FIN/PEND never appear, and the loop spins forever (observed 2+ min stall). Fix: on any
# no-match, sleep and send the kick unconditionally.
OLD_LOOP = '''            elif res == b"":
                time.sleep(10)
                self.wait_write("show jobs processed", wait=None)'''
NEW_LOOP = '''            else:
                # ecloud fix: kick unconditionally on no-match (PAN-OS 12 console is never silent)
                time.sleep(10)
                self.wait_write("show jobs processed", wait=None)'''
assert OLD_LOOP in t, "auto-commit loop anchor not found"
t = t.replace(OLD_LOOP, NEW_LOOP, 1)

open(p, "w").write(t)
print("patched:", p, "(PAN-OS 12: prompt nudge + liberal prompt + unconditional auto-commit kick)")
