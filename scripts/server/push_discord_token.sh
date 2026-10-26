#!/bin/bash
# Run on your Mac after putting a new DISCORD_TOKEN into the local .env:
# copies just that line to the VPS (never shown on screen) and restarts the collector.
# Usage: scripts/server/push_discord_token.sh [server IP]
set -eu
cd "$(dirname "$0")/../.."
HOST="root@${1:-144.172.68.99}"
ENV=/home/zipline/zipline/.env

line=$(grep -E '^DISCORD_TOKEN=.{20,}' .env) || { echo "В локальном .env нет DISCORD_TOKEN — вставьте токен и запустите снова."; exit 1; }
printf '%s\n' "$line" | ssh "$HOST" "set -e; umask 077
  grep -v '^DISCORD_TOKEN=' $ENV > $ENV.new || true
  cat >> $ENV.new
  chown zipline:zipline $ENV.new && mv $ENV.new $ENV
  systemctl restart zipline-live"
echo "Готово: новый токен на сервере, сборщик перезапущен."
