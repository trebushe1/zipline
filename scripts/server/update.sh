#!/bin/bash
# Run by zipline-update.timer as root: pull, and restart the collector only when code changed
# (the collector's own data commits do not count).
set -u
APP=/home/zipline/zipline
before=$(sudo -u zipline git -C "$APP" rev-parse HEAD)
sudo -u zipline git -C "$APP" pull --rebase -q || exit 0
after=$(sudo -u zipline git -C "$APP" rev-parse HEAD)
[ "$before" = "$after" ] && exit 0
# Only the collector's own code needs a restart. Every restart is a fresh Discord login,
# and frequent logins are what got the first collector account banned.
changed=$(sudo -u zipline git -C "$APP" diff --name-only "$before" "$after" -- backend requirements.txt)
[ -z "$changed" ] && exit 0
echo "code changed: $changed"
if echo "$changed" | grep -q '^requirements.txt$'; then
  sudo -u zipline "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
fi
systemctl restart zipline-live.service
