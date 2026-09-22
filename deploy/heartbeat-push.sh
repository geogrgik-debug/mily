#!/usr/bin/env bash
# Publish a capture host's status so someone elsewhere can check on it.
#
# The capture runs on a machine nobody is watching. Nothing else in this
# repository can see that machine, so the machine has to speak: this writes a
# status file and pushes it to a branch that only ever holds status.
#
# Put it on a schedule, e.g. every 5 minutes in crontab:
#
#   */5 * * * * cd /path/to/capture && bash deploy/heartbeat-push.sh >> /tmp/hb.log 2>&1
#
# CREDENTIALS: pushing needs write access from the capture host. Use a deploy
# key scoped to this one repository, not a personal token with account-wide
# reach -- the host is unattended and its only job is to append status.
#
# Set CAPTURE_STATUS_BRANCH to change the branch (default: capture-status),
# and CAPTURE_HOST_NAME to label the host (default: hostname).
#
# Verified from the kind of checkout the capture host actually has -- a
# `--depth 1 --sparse` clone -- against a copy of the remote, both with the
# status branch already present and without it. Two things bit there and are
# handled below: a shallow clone tracks only its own branch, and a new
# worktree inherits the sparse-checkout rules.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BRANCH="${CAPTURE_STATUS_BRANCH:-capture-status}"
HOST="${CAPTURE_HOST_NAME:-$(hostname)}"
DATA="${CAPTURE_DATA_DIR:-data/raw}"
PY="${ROOT}/.venv/bin/python"
WT="${ROOT}/.git/capture-status-worktree"

# A `--depth 1` clone is single-branch: its fetch refspec covers only the
# branch it was cloned from, so a bare `git fetch origin capture-status`
# updates FETCH_HEAD and nothing else, and origin/capture-status never exists.
# Naming both ends of the refspec works from any clone.
REFSPEC="+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"

# The report itself. A non-zero exit means the capture is unhealthy -- publish
# it anyway, since an unhealthy status is the whole point of publishing.
"$PY" -m tennis.ingest.status "$DATA" --out /tmp/capture-status.json || true

# A separate worktree keeps the capture checkout untouched: this must never
# alter the files the recorder is running from.
if [ ! -d "$WT" ]; then
  git fetch -q origin "$REFSPEC" 2>/dev/null || true
  if git rev-parse --verify -q "origin/$BRANCH" >/dev/null; then
    git worktree add -q "$WT" -B "$BRANCH" "origin/$BRANCH"
    fresh=0
  else
    git worktree add -q --detach "$WT"
    fresh=1
  fi
  # A new worktree copies the capture checkout's sparse-checkout rules, and
  # under those rules status/ lies outside the checkout: `git add` refuses the
  # file and `git rm --cached` skips everything else, so the orphan branch
  # below would carry the whole tree. Status is all this worktree holds --
  # switch the rules off for it. The capture checkout keeps its own.
  git -C "$WT" sparse-checkout disable 2>/dev/null || true
  if [ "$fresh" = 1 ]; then
    git -C "$WT" checkout -q --orphan "$BRANCH"
    git -C "$WT" rm -rq --cached . 2>/dev/null || true
    find "$WT" -mindepth 1 -maxdepth 1 -not -name .git -exec rm -rf {} +
  fi
fi

# One file per host, so several capture machines never fight over one file.
mkdir -p "$WT/status"
cp /tmp/capture-status.json "$WT/status/${HOST}.json"

cd "$WT"
git add "status/${HOST}.json"
git -c user.name="capture" -c user.email="capture@localhost" \
    commit -qm "status: ${HOST}" || { echo "nothing changed"; exit 0; }

# Another host may have pushed between our fetch and our push; rebasing onto
# theirs is always right here, because the files never overlap.
for attempt in 1 2 3; do
  if git push -q origin "$BRANCH"; then
    echo "pushed ${HOST} to ${BRANCH}"
    exit 0
  fi
  git fetch -q origin "$REFSPEC" && git rebase -q "origin/$BRANCH" || true
  sleep $((attempt * 2))
done
echo "could not push after 3 attempts" >&2
exit 1
