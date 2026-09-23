# tennis/ingest/onewin

The second book, for one purpose: the lead-lag meter
([`tennis/market/lead_lag.py`](../../market/lead_lag.py)) compares when two
bookmakers move a price, and it needs 1win's prices recorded **on the same
machine** as BetBoom's. It checks that from the logs themselves and refuses
logs from two machines.

**Status: a skeleton.** Config, the endpoints known to answer, the raw log and
a probe work. The odds channel does not exist yet, on purpose: it waits for
one request from devtools, and nothing here guesses at it.

| File | What |
|---|---|
| `client.py` | `OneWinConfig` (API base and partner id, from flags or the environment, no defaults), `OneWinClient` (the known GETs, each answer logged before it is read), `OneWinRecorder` (`probe` works; `run` waits for the odds channel), and `ODDS_TODO` -- what the devtools request will decide |

## What the reconnaissance established

From `HANDOFF.md`, "Второй источник: разведка API 1win":

| Fact | Status |
|---|---|
| The API host is `api-gateway.top-parser.com`; the site (`one-vv2420.com`) is a rotating mirror | established |
| Public: the header `x-external-partner-id` is enough, no session token | checked from the owner's machine and from the sandbox |
| `sports/get?sportId=`, `tournaments/get?tournamentId=`, `matches/get?matchId=` answer | no odds in any of them; tournaments are cached 600 s |
| `markets/get`, `odds/get`, `outcomes/get`, `bets/get`, `lines/get`, `matches/get-markets`, `matches/get-list`, `matches/get?...&withMarkets=true` | 404 or no odds |

Not recorded anywhere: the path on the host in front of `matches/get`, and
the partner id. Both are in any Request URL / request of the devtools session,
so both are config.

## Config

| Variable | Flag | What |
|---|---|---|
| `ONEWIN_API_BASE` | `--api-base` | `https://host/prefix` -- everything in the Request URL before `matches/get` |
| `ONEWIN_PARTNER_ID` | `--partner-id` | the value of `x-external-partner-id` |

No defaults: a host that has rotated away would otherwise fail quietly.

## Probe

```bash
python -m tennis.ingest.onewin.client --probe --match 123456 \
    --api-base https://api-gateway.top-parser.com/PREFIX --partner-id ID
```

Fetches each named sport, tournament and match once, writes the answers to
`data/raw/provider=1win/`, and prints status, size, `cache-control` and the
top-level JSON keys. Run it on the machine that records BetBoom: that is where
the comparison has to come from.

## What unblocks the odds

On a live match page, F12 → Network, the `json` row that takes about ten
seconds (11.36 s when it was seen): its **Request URL** and **Response**.
Headers are not needed; no token goes into the repository or the chat.
`ODDS_TODO` in `client.py` lists what each part decides -- host, the
parameter naming the match, a cursor for the next poll, the hold time, the
shape of a quote. After that: the polling loop here, a decoder
`onewin_quotes` next to `betboom_quotes` in `tennis/market/streams.py`, and
the meter compares the two books.
