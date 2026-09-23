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
| `client.py` | `OneWinConfig` (gateway and partner id, from flags or the environment, no defaults), `OneWinClient` (the REST gateway: the live list, and the objects `--probe` asks for; every answer logged before it is read), `OneWinRecorder` (the push socket: subscribe, log every frame, answer pings, reconnect) |

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
