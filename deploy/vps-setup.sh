#!/usr/bin/env bash
# Set up the BetBoom capture on a fresh Linux host.
#
# Every step below was run end to end on a clean clone before being written
# here: clone, venv, protobuf build, the tests, and a live recorder run that
# subscribed to tennis and wrote a compressed log. Re-run 22.09.2026 on the
# sparse clone deploy/README.md prescribes, which is what a capture host has.
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
command -v curl >/dev/null || { echo "  curl is missing: sudo apt install -y curl" >&2; exit 1; }
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
set +e
./.venv/bin/python -m pytest tennis/ -q
rc=$?
set -e
if [ "$rc" -eq 5 ]; then
  # pytest's "no tests collected". The sparse checkout in deploy/README.md
  # must include tennis/tests/ -- without it this used to stop here silently.
  echo "  pytest found no tests: tennis/tests/ is missing from this checkout." >&2
  echo "  On a sparse clone run:  git sparse-checkout add '/tennis/tests/**'" >&2
  exit 1
elif [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

# --- 4. the one directory the service may write --------------------------------
# The unit confines writes to data/ (ReadWritePaths=), and systemd refuses to
# start a unit whose ReadWritePaths= does not exist. Creating the empty
# directory is the only thing this script ever does under data/.
mkdir -p data/raw
echo
echo "== data/raw ready =="

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

Disk: 0.3-0.9 GB a day for ten matches, measured. A month is 9-27 GB, so
check free space before leaving it unattended.
TXT
