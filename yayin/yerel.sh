#!/bin/zsh
# Mac backup for the GitHub queue (launchd every 5 min). Posts only items 10+ min late.
cd "$(dirname "$0")/.." || exit 1
git pull -q --rebase || exit 1
COMPOSIO_API_KEY="$(security find-generic-password -s ya-tersiyse.composio -w)" GRACE_MIN=10 /usr/bin/python3 yayin/publish.py
git add yayin/queue.json
git diff --cached --quiet || { git commit -qm "yayın sırası: durum (Mac)"; git pull -q --rebase && git push -q; }
