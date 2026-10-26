#!/bin/bash
# Run on your Mac: puts the collector's secrets into .env on the VPS without showing them.
#   - Discord and Telegram settings are taken from the local .env
#   - a new Firebase service-account key is created with gcloud (like scripts/firebase_key.sh)
#   - the GitHub token is the one gh is logged in with on this Mac
#   - your private Telegram chat id is read from the bot's updates (send the bot any message first)
#   - only the Steam login and password are asked for here
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

gh_token=$(gh auth token)
bot_token=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2-)
admin_chat=$(curl -s "https://api.telegram.org/bot$bot_token/getUpdates" | python3 -c '
import json, sys
try:
    updates = json.load(sys.stdin).get("result", [])
except ValueError:
    updates = []
chats = [u["message"]["chat"]["id"] for u in updates if u.get("message", {}).get("chat", {}).get("type") == "private"]
print(chats[-1] if chats else "")')
if [ -n "$admin_chat" ]; then
  echo "Личный чат для сообщений о сбоях найден."
else
  echo "Личный чат не найден (напишите боту позже) — сообщения о сбоях пока будут только в логах."
fi
read -r -p "Steam логин: " steam_user
read -r -s -p "Steam пароль (не отображается): " steam_pass; echo
{
  echo "STEAM_USERNAME=$steam_user"
  echo "STEAM_PASSWORD=$steam_pass"
  echo "GH_TOKEN=$gh_token"
  if [ -n "$admin_chat" ]; then echo "TELEGRAM_ADMIN_CHAT_ID=$admin_chat"; fi
} >> "$OUT"

ssh "$HOST" "umask 077; cat > $ENV && chown zipline:zipline $ENV && chmod 600 $ENV" < "$OUT"
echo "Готово: секреты сохранены на сервере ($(wc -l < "$OUT" | tr -d ' ') строк)."
