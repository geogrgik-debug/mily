# tennis/ingest/betboom

Recorder for the BetBoom live betting line, and the tooling that made it
possible. The reasoning and the measurements are in
[`docs/TRACK_A_betboom_capture.md`](../../../docs/TRACK_A_betboom_capture.md).

| File | What |
|---|---|
| `extract_schema.py` | Recovers the feed's protobuf schema from the shipped JS bundle. Re-run on every `APP_BUILD` change. |
| `check_build.py` | Checks the live widget against what the recorder assumes: the socket uuid (`DEFAULT_URL`) and the schema (`proto/schema.json`), field by field. Exit 1 names what moved. |
| `proto/` | The recovered schema. 223 messages, 1012 fields, 25 enums. Compiles under `protoc`. |
| `client.py` | The recorder: subscribes to live tennis, writes every frame raw, follows the pushed snapshots, and subscribes to game outcomes one by one so a reprice inside a game is recorded too. |
| `generated/` | protoc output. Not in git — rebuild it. |

## Short version

BetBoom's sportsbook is a white-label of sporthub.bet. The line is served
**only** over `wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1` as protobuf, as a
per-match subscription: a full snapshot of the match's markets, then a fresh
full snapshot on every score change (median 13.5 s apart). Per-price messages
(`newsletters_stake`) exist too, but only for outcomes named in a
`stakes_subscribe`; since 3f695d8 the recorder sends one for every game
outcome, because without it a reprice during a rally never reached the log.
Two earlier drafts here got this wrong in opposite directions: the first said
per-price pushes arrive unasked, the second that there are none.
There is no HTTP line endpoint.

That makes volume a question of staying connected rather than polling harder,
and it means the feed hands over the **score and the serving side alongside the
prices**, from one socket with one clock — the pair, not half of it.

## Running it

```bash
pip install grpcio-tools websockets
mkdir -p tennis/ingest/betboom/generated      # protoc does not create it
python -m grpc_tools.protoc -I tennis/ingest/betboom/proto \
    --python_out=tennis/ingest/betboom/generated \
    tennis/ingest/betboom/proto/bb_sport_ws_v1.proto

python -m tennis.ingest.betboom.client --discover --max-matches 3
```

Needs a host that can open a WebSocket. An HTTP CONNECT proxy that passes the
upgrade is fine -- the agent sandbox's does, measured, after a first draft here
claimed otherwise.

## What is verified and what is not

Verified offline, with tests: the schema compiles and round-trips, the recorder
speaks it, a tennis match in the tree gets a full-market subscription, the
concurrency cap holds, every frame reaches the raw log before parsing, and a
malformed frame neither kills the session nor is lost.

Verified live on 22.09.2026 (`docs/TRACK_A_betboom_capture.md`): no handshake
beyond the subscription is demanded; 10 concurrent `subscribe_full`
subscriptions were all accepted with code 200; game-winner markets exist at
every level, the exact-score market from Challenger and WTA 125 upward, and
WTT/ITF carry only the two-way game market.

Seen live on 23.09.2026: the server checks the handshake headers -- without
`Origin` or `User-Agent` it closes the socket at once with 3009 "Access
rejected". From about 11:37 to 11:51 MSK it also closed every session within a
second of the handshake with 3010 "Access rejected", from two unrelated
addresses, then accepted again with nothing changed on our side. The recorder
backs off such refusals (`HEALTHY_SESSION_S`) and reports the last close reason
as `last_disconnect` in its sidecar and in the machine's status report.
