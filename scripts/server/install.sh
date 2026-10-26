#!/bin/bash
# Sets up zipline on our VPS (Ubuntu, run as root). Safe to run again.
#   - zipline-live: the live collector (Discord, Reddit, Steam builds -> patch pipeline), restarted on failure
#   - zipline-update: every 5 minutes pulls the repo and restarts the collector when code changed
# Secrets live in /home/zipline/zipline/.env (see .env.example), owned by zipline, mode 600.
set -eu
APP=/home/zipline/zipline
TOOLS=/opt/zipline-tools/exe

sudo -u zipline git -C "$APP" config user.name "zipline-bot"
sudo -u zipline git -C "$APP" config user.email "zipline-bot@users.noreply.github.com"
sudo -u zipline git -C "$APP" remote set-url origin git@github.com:trebushe1/zipline.git
sudo -u zipline "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt" websockets certifi
chmod 600 "$APP/.env"

cat > /etc/systemd/system/zipline-live.service <<UNIT
[Unit]
Description=zipline live collector and Deadlock build watcher
After=network-online.target
Wants=network-online.target

[Service]
User=zipline
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=PUBLISH=1 PATCH_PIPELINE=local RUN_SECONDS=0
Environment=SFD_PATH=$TOOLS/SteamFileDownloader/SteamFileDownloader VRF_PATH=$TOOLS/Source2Viewer/Source2Viewer-CLI
ExecStart=$APP/.venv/bin/python -m backend.live
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/zipline-update.service <<UNIT
[Unit]
Description=Pull zipline code and restart the collector when it changed

[Service]
Type=oneshot
ExecStart=$APP/scripts/server/update.sh
UNIT

cat > /etc/systemd/system/zipline-update.timer <<UNIT
[Unit]
Description=Pull zipline code every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now zipline-live.service zipline-update.timer
systemctl --no-pager --lines=0 status zipline-live.service
