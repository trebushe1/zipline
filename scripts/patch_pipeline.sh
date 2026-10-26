#!/bin/bash
# The patch pipeline on our server, the same steps as .github/workflows/patches.yml:
# new build from Steam -> open pages (Firebase) -> Telegram -> site, then before/after media
# -> open pages -> Telegram edit -> site.
# Started by backend/steam_watch.py; one run at a time.
set -u
cd "$(dirname "$0")/.."
exec 9>/tmp/zipline-patch-pipeline.lock
flock 9
PY=.venv/bin/python
failed=0
git pull --rebase -q || true
$PY -m backend.tracker || failed=1
$PY -m backend.patches_live  # open pages show the build at once, before the deploy
$PY -m backend.build_alerts
scripts/publish_patches.sh "new Deadlock build summaries"
$PY -m backend.patches || true  # SteamDB's history, when it catches up
$PY -m backend.media || failed=1
$PY -m backend.patches_live
$PY -m backend.build_alerts --media
scripts/publish_patches.sh "Deadlock build media"
exit $failed
