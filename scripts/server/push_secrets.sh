#!/bin/bash
# Run on your Mac: puts the collector's secrets into .env on the VPS without showing them.
#   - Discord and Telegram settings are taken from the local .env
#   - a new Firebase service-account key is created with gcloud (like scripts/firebase_key.sh)
#   - Steam login, the GitHub token and your private Telegram chat id are asked for here
# Usage: scripts/server/push_secrets.sh 144.172.68.99
set -eu
cd "$(dirname "$0")/../.."
HOST="root@${1:?server IP}"
ENV=/home/zipline/zipline/.env
GCLOUD=$(command -v gcloud || echo /opt/homebrew/bin/gcloud)
SA=zipline-collector@zipline-272dd.iam.gserviceaccount.com

OUT=$(mktemp)
KEY=$(mktemp)
trap 'rm -f "$OUT" "$KEY"' EXIT

grep -E '^(DISCORD_TOKEN|DISCORD_GUILD_ID|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|REDDIT_CLIENT_ID|REDDIT_CLIENT_SECRET)=' .env > "$OUT" || true
echo "FIREBASE_DB_URL=https://zipline-272dd-default-rtdb.europe-west1.firebasedatabase.app" >> "$OUT"
"$GCLOUD" iam service-accounts keys create "$KEY" --iam-account="$SA" --quiet 2>/dev/null
printf 'FIREBASE_SERVICE_ACCOUNT=%s\n' "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "$KEY")" >> "$OUT"

read -r -p "Steam логин: " steam_user
read -r -s -p "Steam пароль (не отображается): " steam_pass; echo
read -r -s -p "GitHub токен (не отображается): " gh_token; echo
read -r -p "Ваш личный Telegram chat id для сообщений о сбоях (Enter — пропустить): " admin_chat
{
  echo "STEAM_USERNAME=$steam_user"
  echo "STEAM_PASSWORD=$steam_pass"
  echo "GH_TOKEN=$gh_token"
  [ -n "$admin_chat" ] && echo "TELEGRAM_ADMIN_CHAT_ID=$admin_chat"
} >> "$OUT"

ssh "$HOST" "umask 077; cat > $ENV && chown zipline:zipline $ENV && chmod 600 $ENV" < "$OUT"
echo "Готово: секреты сохранены на сервере ($(wc -l < "$OUT" | tr -d ' ') строк)."
