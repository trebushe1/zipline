#!/bin/bash
# Commit new build summaries and redeploy the site (used by scripts/patch_pipeline.sh
# on the server and by .github/workflows/patches.yml).
set -u
git add data/patches
if git diff --cached --quiet; then exit 0; fi
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  git config user.name "zipline-bot"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
fi
git commit -q -m "data: ${1:-Deadlock build summaries}"
for i in 1 2 3; do
  git pull --rebase -q && git push -q && break
  sleep 5
done
# A push from the server's deploy key starts deploy.yml by itself; one made with
# GitHub Actions' own token does not, so there it is started explicitly.
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  gh workflow run deploy.yml -R "$GITHUB_REPOSITORY"
fi
