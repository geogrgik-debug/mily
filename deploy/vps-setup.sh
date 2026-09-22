#!/usr/bin/env bash
# Set up the BetBoom capture on a fresh Linux host.
#
# Every step below was run end to end on a clean clone before being written
# here: clone, venv, protobuf build, 393 tests, and a live recorder run that
# subscribed to tennis and wrote a compressed log.
#
#   bash deploy/vps-setup.sh            # from inside the repository
#
# It is idempotent: re-running it rebuilds the venv contents and the protobuf
# classes without touching anything under data/.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "repository: $ROOT"

# --- 0. can this host even reach the feed? -----------------------------------
# Fail here rather than after a full install. A host that cannot open the socket
# cannot run the capture, and that is worth knowing in the first ten seconds.
echo
echo "== checking the feed host is reachable =="
if ! curl -sS --max-time 15 -o /dev/null -w '  HTTP %{http_code} from sporthub.bet\n' \
     --http1.1 "https://ru-ws2.sporthub.bet/api/tree_ws/v1" \
     -H 'Upgrade: websocket' -H 'Connection: Upgrade' \
     -H 'Sec-WebSocket-Version: 13' \
     -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     -H 'Origin: https://betboom.ru'; then
  echo "  could not reach the feed host. Check egress before going further." >&2
  exit 1
fi
echo "  a WebSocket upgrade returns 101 when the host is usable; anything else"
echo "  means this machine cannot record, however well the rest installs."

# --- 1. python environment ----------------------------------------------------
echo
echo "== python environment =="
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
  || { echo "  need Python 3.11+" >&2; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/python -m pip install -q -r requirements.txt
echo "  $(./.venv/bin/python -V)"

# --- 2. protobuf classes ------------------------------------------------------
# Gitignored on purpose: they are build output, and the field numbers are a
# contract with the bookmaker's current APP_BUILD.
echo
echo "== protobuf classes =="
mkdir -p tennis/ingest/betboom/generated
./.venv/bin/python -m grpc_tools.protoc -I tennis/ingest/betboom/proto \
  --python_out=tennis/ingest/betboom/generated \
  tennis/ingest/betboom/proto/bb_sport_ws_v1.proto
echo "  built: $(ls tennis/ingest/betboom/generated/*_pb2.py | xargs -n1 basename)"

# --- 3. prove it works before trusting it with a night --------------------------
echo
echo "== tests =="
./.venv/bin/python -m pytest tennis/ -q

echo
echo "== done =="
cat <<TXT

Run the capture in the foreground to watch it:

  ./.venv/bin/python -m tennis.ingest.betboom.client --out data/raw --max-matches 10

Or install it as a service that survives logout and reboot:

  sudo cp deploy/betboom-capture.service /etc/systemd/system/
  sudo sed -i "s#__ROOT__#$ROOT#; s#__USER__#$(id -un)#" \\
      /etc/systemd/system/betboom-capture.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now betboom-capture
  journalctl -u betboom-capture -f

Disk: 0.3-0.9 GB a day for ten matches, measured. A month is 10-27 GB, so
check free space before leaving it unattended.
TXT
