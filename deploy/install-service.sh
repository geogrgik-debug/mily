#!/usr/bin/env bash
# Steps 4 and 5 of deploy/README.md in one command: install the systemd unit
# for this checkout, start it, schedule the heartbeat, and publish the first
# status once the recorder has had time to write.
#
#   bash deploy/install-service.sh        # after deploy/vps-setup.sh
#
# Why a script: the manual steps carry `__ROOT__`, `$(pwd)` and `*/5 * * * *`,
# and a command copied out of a chat loses exactly those characters -- the chat
# takes them for markup. On 22.09 an owner's paste arrived with `__init__`
# turned into `init` and every `**` gone. A path with no such characters
# survives any copy.
#
# Re-running it is safe: the unit is rewritten, the service restarted, and the
# heartbeat line in crontab replaced rather than duplicated.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RUN_AS="$(id -un)"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
UNIT=/etc/systemd/system/betboom-capture.service

[ -x .venv/bin/python ] || { echo "no .venv here: run  bash deploy/vps-setup.sh  first" >&2; exit 1; }
# The unit confines writes to data/, and systemd refuses to start it without one.
mkdir -p data/raw

echo "== service: $UNIT (runs as $RUN_AS from $ROOT)"
$SUDO cp deploy/betboom-capture.service "$UNIT"
$SUDO sed -i "s#__ROOT__#${ROOT}#g; s#__USER__#${RUN_AS}#g" "$UNIT"
$SUDO systemctl daemon-reload
$SUDO systemctl enable betboom-capture
$SUDO systemctl restart betboom-capture

echo "== heartbeat: every 5 minutes into the capture-status branch"
LINE="*/5 * * * * cd ${ROOT} && bash deploy/heartbeat-push.sh >> /tmp/hb.log 2>&1"
{ crontab -l 2>/dev/null | grep -v 'deploy/heartbeat-push.sh' || true; echo "$LINE"; } | crontab -
crontab -l | grep 'deploy/heartbeat-push.sh'

echo "== waiting 90 s for the first records"
sleep 90
if ! systemctl is-active --quiet betboom-capture; then
  echo "the service is not running. Send all of the output below:" >&2
  systemctl status betboom-capture --no-pager 2>&1 | head -20 || true
  journalctl -u betboom-capture -n 40 --no-pager 2>&1 || true
  exit 1
fi
echo "service: active"
journalctl -u betboom-capture -n 15 --no-pager 2>&1 || true

echo "== first status report"
bash deploy/heartbeat-push.sh

echo
echo "Done. The capture runs by itself and comes back after a reboot."
echo "Check it any time:  systemctl status betboom-capture --no-pager"
