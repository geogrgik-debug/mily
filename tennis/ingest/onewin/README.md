# tennis/ingest/onewin

The second book, for one purpose: the lead-lag meter
([`tennis/market/lead_lag.py`](../../market/lead_lag.py)) compares when two
bookmakers move a price, and it needs 1win's prices recorded **on the same
machine** as BetBoom's. It checks that from the logs themselves and refuses
logs from two machines.

**Status: records.** Live tennis singles, every market, from 1win's push
server; checked live from the owner's laptop on 23.09 (21 live singles found,
20 subscribed, 408 frames in 90 s, no reconnect).

| File | What |
|---|---|
| `client.py` | `OneWinConfig` (gateway and partner id, from flags or the environment, no defaults), `OneWinClient` (the REST gateway: the live list, and the objects `--probe` asks for; every answer logged before it is read), `OneWinRecorder` (the push socket: subscribe, log every frame, answer pings, reconnect, write its counters) |

## How its prices travel

Found on 23.09 by listening to a live match page in a clean browser profile,
then repeating each call from plain Python.

* The "11 s json request" the first reconnaissance chased was **Kaspersky's
  web antivirus** long-polling from inside the page
  (`gc.kis.v2.scr.kaspersky-labs.com/.../longp`), not 1win.
* Prices come over a **socket.io websocket** (Engine.IO 4) on the gateway:
  `wss://api-gateway.top-parser.com/push-server-v2/?Language=ru&externalPartnerId=<id>&EIO=4&transport=websocket`.
  Open `0{...}`, answer `40`, connected `40{...}`; the server pings `2`
  every 25 s, the answer is `3`. No header needed.
* Subscribe by match id (the number ending a match page's address) with
  `subscribe-match-info` and `subscribe-match-odds` (`isBaseOddsGroups:
  false` for every market). The board comes as `match-odds-snapshot`, then
  `match-odds` with what changed; each odds item has a stable id, `cf`,
  `status` (1 open, 2 suspended) and the server's `ts` in ms. Names, outcome
  and set/game numbers come with the snapshot and when an item first appears.
* Live matches: `POST matches/get-many {"service":"live","sportIds":[33]}`.
  REST needs a browser User-Agent (403 without); `x-lang: ru` gives Russian
  names beside the Latin slugs.
* The API base has no path prefix: `https://api-gateway.top-parser.com`.

The decoder is `onewin_quotes` in
[`tennis/market/streams.py`](../../market/streams.py); matches are paired
with BetBoom's by the players' names in
[`tennis/market/join.py`](../../market/join.py).

## Config

| Variable | Flag | What |
|---|---|---|
| `ONEWIN_API_BASE` | `--api-base` | `https://api-gateway.top-parser.com` -- everything before `matches/get` |
| `ONEWIN_PARTNER_ID` | `--partner-id` | the value of `x-external-partner-id` any browser on the site sends |

No defaults: a host that has rotated away would otherwise fail quietly. The
partner id is not a secret -- every visitor's browser sends it -- but it stays
out of the repository and out of the log.

## Run

```bash
python -m tennis.ingest.onewin.client --out data/raw            # until stopped
python -m tennis.ingest.onewin.client --out data/raw --seconds 900 --max-matches 25
python -m tennis.ingest.onewin.client --probe --match 40403794  # one REST look
```

Every frame of the socket is on disk before it is read; a reconnect writes a
`_conn` row, which the meter reads as a break. `[hb]` once a minute says what
has arrived. Run it on the machine that records BetBoom.

## Counters: recording, or idling

A growing log does not prove that prices arrive. The server pings every 25 s,
and the score (`match-info`) comes about as often as prices: 1469 messages
to 1571 in the first 15 minutes, 23.09. A socket that has lost its
subscriptions keeps writing both. BetBoom's capture stood idle a night that
way (22-23.09). So the recorder writes its own counters beside the log, at
start and then once a minute:

    <out>/provider=1win/_counters-<run id>.json

They sit in 1win's own folder, and the name is not `_recorder*.json`, because
`tennis.ingest.status` reads the newest such file in the log root as
BetBoom's. The file is written whole and then renamed, so a reader never gets
half of it. A failed write is skipped, and the log goes on. A clean stop
removes the file. Only a killed process leaves one behind, and its
`written_at_s` shows how old it is.

| Field | What |
|---|---|
| `provider`, `run_id` | `1win`, and the run: the same id as the run's log files |
| `written_at_s` | when the file was written, wall clock, seconds |
| `live` | live singles in the gateway's last answer; 0 at a quiet hour is no fault |
| `subscribed` | matches subscribed on the current socket; 0 right after a drop, until the resubscription |
| `quotes_last_hour` | quotes in the last 60 minutes, counted by minute of the monotonic clock |
| `quotes_total` | quotes since the run started |
| `last_quote_at_s` | when the last quote came, wall clock; null before the first |
| `frames` | every row written, pings and the score included -- for comparison |
| `reconnects`, `last_disconnect` | as in `[hb]`, the partner id replaced |

A **quote** is one outcome's odds item, in `match-odds-snapshot` (a match's
whole board) or `match-odds` (what changed). No other message carried any on
23.09. Pings, the score and the answers to a subscription count as none.

Reading them: `subscribed` 0 while `live` is not means nothing is subscribed;
`last_quote_at_s` long past while `subscribed` is not means the socket brings
no prices; a `written_at_s` minutes old means the process is gone.
