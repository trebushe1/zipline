#!/bin/bash
# Commit new build summaries and redeploy the site (used by .github/workflows/patches.yml).
set -u
git add data/patches
if git diff --cached --quiet; then exit 0; fi
git config user.name "zipline-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -q -m "data: ${1:-Deadlock build summaries}"
for i in 1 2 3; do
  git pull --rebase -q && git push -q && break
  sleep 5
done
gh workflow run deploy.yml -R "$GITHUB_REPOSITORY"
