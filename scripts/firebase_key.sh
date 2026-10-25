#!/bin/sh
# Creates a key for the live collector's Firebase service account and stores it
# as the FIREBASE_SERVICE_ACCOUNT secret on GitHub. The key never touches the screen
# and the temporary file is deleted right away.
set -e
cd "$(dirname "$0")/.."
GCLOUD=$(command -v gcloud || echo /opt/homebrew/bin/gcloud)
SA=zipline-collector@zipline-272dd.iam.gserviceaccount.com

KEY=$(mktemp)
trap 'rm -f "$KEY"' EXIT
"$GCLOUD" iam service-accounts keys create "$KEY" --iam-account="$SA" --quiet 2>/dev/null
gh secret set FIREBASE_SERVICE_ACCOUNT < "$KEY"
echo "Готово: ключ Firebase сохранён в GitHub."
