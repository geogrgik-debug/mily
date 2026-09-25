#!/usr/bin/env bash
# Step 7 of deploy/README.md in one command: install and start the 1win capture
# beside the BetBoom one. BetBoom's unit and the heartbeat line are not touched.
#
#   bash deploy/install-onewin.sh        # after /etc/mily/onewin.env exists
#
# Re-running it is safe: the unit is rewritten and the service restarted.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RUN_AS="$(id -un)"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
UNIT=/etc/systemd/system/onewin-capture.service
ENV_FILE=/etc/mily/onewin.env

[ -x .venv/bin/python ] || { echo "no .venv here: run  bash deploy/vps-setup.sh  first" >&2; exit 1; }
# Checked, never printed: the value stays out of the terminal and the journal.
$SUDO grep -q '^ONEWIN_PARTNER_ID=.' "$ENV_FILE" 2>/dev/null || {
  echo "no ONEWIN_PARTNER_ID line in $ENV_FILE: create it first, deploy/README.md step 7" >&2
  exit 1
}
# The unit confines writes to data/, and systemd refuses to start it without one.
mkdir -p data/raw

echo "== service: $UNIT (runs as $RUN_AS from $ROOT)"
$SUDO cp deploy/onewin-capture.service "$UNIT"
$SUDO sed -i "s#__ROOT__#${ROOT}#g; s#__USER__#${RUN_AS}#g" "$UNIT"
$SUDO systemctl daemon-reload
$SUDO systemctl enable onewin-capture
$SUDO systemctl restart onewin-capture

echo "== waiting 90 s for the first subscriptions and heartbeat"
sleep 90
if ! systemctl is-active --quiet onewin-capture; then
  echo "the service is not running. Send all of the output below:" >&2
  systemctl status onewin-capture --no-pager 2>&1 | head -20 || true
  journalctl -u onewin-capture -n 40 --no-pager 2>&1 || true
  exit 1
fi
echo "service: active"
journalctl -u onewin-capture -n 15 --no-pager 2>&1 || true

echo
echo "Done. Check it any time:  journalctl -u onewin-capture -n 20 --no-pager"
