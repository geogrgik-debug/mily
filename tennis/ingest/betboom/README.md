# tennis/ingest/betboom

Recorder for the BetBoom live betting line, and the tooling that made it
possible. The reasoning and the measurements are in
[`docs/TRACK_A_betboom_capture.md`](../../../docs/TRACK_A_betboom_capture.md).

| File | What |
|---|---|
| `extract_schema.py` | Recovers the feed's protobuf schema from the shipped JS bundle. Re-run on every `APP_BUILD` change. |
| `proto/` | The recovered schema. 223 messages, 1012 fields, 25 enums. Compiles under `protoc`. |
| `client.py` | The recorder: subscribes to live tennis, writes every frame raw, follows the delta pushes. |
| `generated/` | protoc output. Not in git — rebuild it. |

## Short version

BetBoom's sportsbook is a white-label of sporthub.bet. The line is served
**only** over `wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1` as protobuf, as a
delta subscription: one full snapshot per match, then one message per price
change. There is no HTTP line endpoint.

That makes volume a question of staying connected rather than polling harder,
and it means the feed hands over the **score and the serving side alongside the
prices**, from one socket with one clock — the pair, not half of it.

## Running it

```bash
pip install grpcio-tools websockets
python -m grpc_tools.protoc -I tennis/ingest/betboom/proto \
    --python_out=tennis/ingest/betboom/generated \
    tennis/ingest/betboom/proto/bb_sport_ws_v1.proto

python -m tennis.ingest.betboom.client --discover --max-matches 3
```

Needs a host that can open a WebSocket: it will not run behind an HTTP CONNECT
proxy that does not pass upgrades.

## What is verified and what is not

Verified offline, with tests: the schema compiles and round-trips, the recorder
speaks it, a tennis match in the tree gets a full-market subscription, the
concurrency cap holds, every frame reaches the raw log before parsing, and a
malformed frame neither kills the session nor is lost.

Not verified, and only one live session away: whether the server demands more of
the handshake, whether game markets are offered on Challenger and ITF events,
and how many concurrent `subscribe_full` subscriptions it tolerates. Start
`--max-matches` low.
