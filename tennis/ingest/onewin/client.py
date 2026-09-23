"""Recorder skeleton for the 1win line: config, the known endpoints, a raw log.

Why 1win at all: the lead-lag meter (`tennis.market.lead_lag`) needs a second
book's prices recorded on the same machine as BetBoom's, and 1win is the one
half-explored. Everything below is what the reconnaissance in HANDOFF.md
("Второй источник: разведка API 1win") established; nothing is guessed.

What is known
-------------
* The API lives on `api-gateway.top-parser.com`; the site (`one-vv2420.com`
  when it was looked at) is a rotating mirror. Neither is baked in here.
* It is public: the header `x-external-partner-id` is enough, no session token
  -- checked from the owner's machine and from the agent sandbox.
* `sports/get?sportId=` (a 90-byte reference), `tournaments/get?tournamentId=`
  (a tournament's structure, served with `cache-control: max-age=600`, so no
  use live) and `matches/get?matchId=` (453 bytes: line-ups, slugs,
  `service: "LIVE"`, `updatedAt` in milliseconds) answer. **None carries odds.**
* `markets/get`, `odds/get`, `outcomes/get`, `bets/get`, `lines/get`,
  `matches/get-markets`, `matches/get-list` and `matches/get?...&withMarkets=true`
  are 404 or carry no odds either.

What is not, and what will tell
-------------------------------
Where the odds come from. `top-parser` is a content gateway, not a price feed.
The likeliest carrier is a long poll -- two `json` requests of 11.36 s in
devtools, which is also why the Socket filter there was empty. One such request
copied from devtools on a match page, its Request URL and its Response, settles
it; `ODDS_TODO` lists what each part decides. Until then `odds_channel` and
`OneWinRecorder.run` refuse rather than guess.

Also not recorded: the path on the host in front of `matches/get`. It is in
every Request URL of the same devtools session, so the whole base is config.

    python -m tennis.ingest.onewin.client --probe --match 123456 \\
        --api-base https://api-gateway.top-parser.com/PREFIX --partner-id ID

`--probe` fetches the known endpoints once, writes the answers to the raw log
and prints what came back. It checks the config from the machine it runs on;
it records no prices.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

from tennis.ingest.rawlog import RawLog

PROVIDER = "1win"
ENV_API_BASE = "ONEWIN_API_BASE"
ENV_PARTNER_ID = "ONEWIN_PARTNER_ID"
PARTNER_HEADER = "x-external-partner-id"

# The endpoints that answered during reconnaissance, and the query parameter
# each takes. None of them carries a price.
SPORT = "sports/get"
TOURNAMENT = "tournaments/get"
MATCH = "matches/get"

ODDS_TODO = """\
1win odds channel: not implemented -- waiting for one request from devtools.

On a live match page, F12 -> Network, the row of type `json` that takes about
ten seconds (11.36 s when it was seen). Copy its Request URL and its Response;
the headers are not needed, and no token goes into the repository or the chat.
What each part decides:

1. Request URL, host and path: whether prices come from the same gateway as
   the known endpoints or from another host -- then that host goes into config
   too, like ONEWIN_API_BASE.
2. Request URL, query: which parameter names the match (one or several at
   once), and whether one names a position in the stream -- a version, a
   timestamp, an offset -- that the next request must send back. That makes it
   a long poll to re-issue at once with the new position, not a timer.
3. The ~11 s: the server's hold time. An answer before it is a change; one at
   the full hold is "nothing changed". Both are logged: the empty one is the
   heartbeat that tells a quiet match from a dead recorder.
4. Response body: where the match, market, outcome, odds and the suspension
   flag sit; whether an answer is the whole board or only what changed; and
   whether it carries its own timestamp -- that goes to ts_source_ns, beside
   ours, as the BetBoom recorder does.

Then: this method polls and logs; `onewin_quotes` goes into
tennis/market/streams.py next to `betboom_quotes`; and the lead-lag meter
compares the two books -- from logs written on one machine, which it checks.
"""


class ConfigError(ValueError):
    """The recorder was not told where the API is or who it is."""


@dataclass(frozen=True)
class OneWinConfig:
    """Where the API is and whom the requests are from.

    Nothing has a default. Hosts rotate like any bookmaker's -- the site's
    mirror already has -- so a stale host in the code would fail quietly in
    the one way a recorder must not; and the partner id is the owner's to give.
    """

    api_base: str        # scheme, host and path prefix, without a trailing slash
    partner_id: str
    timeout_s: float = 15.0

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None, *, api_base: str | None = None,
             partner_id: str | None = None, timeout_s: float = 15.0) -> "OneWinConfig":
        """Arguments first, then the environment; fail naming what is missing."""
        env = os.environ if env is None else env
        base = (api_base or env.get(ENV_API_BASE) or "").strip()
        partner = (partner_id or env.get(ENV_PARTNER_ID) or "").strip()
        missing = [name for name, value in ((ENV_API_BASE, base), (ENV_PARTNER_ID, partner))
                   if not value]
        if missing:
            raise ConfigError(f"missing {', '.join(missing)}: set it in the environment "
                              "or pass --api-base / --partner-id "
                              "(see tennis/ingest/onewin/README.md)")
        parts = urlsplit(base)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise ConfigError(f"{ENV_API_BASE} must be https://host[/prefix] with no "
                              f"query, got {base!r}")
        return cls(base.rstrip("/"), partner, timeout_s)


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    cache_control: str | None = None

    def json(self):
        return json.loads(self.body)


# (url, headers, timeout_s) -> (status, response headers, body)
Transport = Callable[[str, dict, float], tuple[int, Mapping[str, str], bytes]]


def urllib_transport(url: str, headers: dict, timeout_s: float):
    """GET with the standard library. An HTTP error is an answer, not a crash:
    404 was a finding of the reconnaissance, and it belongs in the log."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers or {}, err.read()


class OneWinClient:
    """The endpoints that are known to answer. Every answer is on disk before
    anything reads it -- the collector's first rule, as for BetBoom."""

    def __init__(self, config: OneWinConfig, log: RawLog,
                 transport: Transport = urllib_transport):
        self.config = config
        self.log = log
        self.transport = transport

    def get(self, endpoint: str, **params) -> Response:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.config.api_base}/{endpoint}" + (f"?{query}" if query else "")
        headers = {PARTNER_HEADER: self.config.partner_id, "Accept": "application/json"}
        self.log.write(url, direction="tx", channel=endpoint)
        status, resp_headers, body = self.transport(url, headers, self.config.timeout_s)
        cache = {k.lower(): v for k, v in dict(resp_headers).items()}.get("cache-control")
        self.log.write(body, direction="rx", channel=endpoint,
                       meta={"status": status, "url": url, "cache_control": cache})
        return Response(url, status, body, cache)

    def sport(self, sport_id) -> Response:
        return self.get(SPORT, sportId=sport_id)

    def tournament(self, tournament_id) -> Response:
        return self.get(TOURNAMENT, tournamentId=tournament_id)

    def match(self, match_id) -> Response:
        return self.get(MATCH, matchId=match_id)

    def odds_channel(self, match_id):
        """The prices. Not implemented: `ODDS_TODO` says what will make it so."""
        raise NotImplementedError(ODDS_TODO)


class OneWinRecorder:
    """Same shape as the BetBoom recorder: one raw log, one loop, one report.

    Today only `probe` works. `run` is the recording loop, and it needs the
    odds channel.
    """

    def __init__(self, client: OneWinClient):
        self.client = client

    def probe(self, *, sports=(), tournaments=(), matches=()) -> list[Response]:
        """Fetch each named object once. Proves the config from this machine."""
        out = [self.client.sport(i) for i in sports]
        out += [self.client.tournament(i) for i in tournaments]
        out += [self.client.match(i) for i in matches]
        return out

    def run(self) -> None:
        raise NotImplementedError(ODDS_TODO)


def describe(resp: Response) -> str:
    """One line per answer: status, size, caching, and its top-level keys --
    read off the body, not assumed."""
    line = f"{resp.status}  {len(resp.body):6d} B  cache-control={resp.cache_control}  {resp.url}"
    try:
        doc = resp.json()
    except ValueError:
        return line + "\n      body is not JSON"
    if isinstance(doc, dict):
        return line + f"\n      keys: {', '.join(sorted(doc))}"
    return line + f"\n      top level: {type(doc).__name__}"


def main(argv: list[str] | None = None, *, transport: Transport = urllib_transport) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/raw", help="raw log root")
    ap.add_argument("--api-base", help=f"https://host/prefix; else ${ENV_API_BASE}")
    ap.add_argument("--partner-id", help=f"else ${ENV_PARTNER_ID}")
    ap.add_argument("--probe", action="store_true",
                    help="fetch the known endpoints once and print what came back")
    ap.add_argument("--sport", action="append", default=[], metavar="ID")
    ap.add_argument("--tournament", action="append", default=[], metavar="ID")
    ap.add_argument("--match", action="append", default=[], metavar="ID")
    args = ap.parse_args(argv)

    if not args.probe:
        print(ODDS_TODO, file=sys.stderr)
        return 2
    if not (args.sport or args.tournament or args.match):
        ap.error("--probe needs at least one --sport, --tournament or --match id")
    try:
        config = OneWinConfig.load(api_base=args.api_base, partner_id=args.partner_id)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    with RawLog(args.out, provider=PROVIDER) as log:
        recorder = OneWinRecorder(OneWinClient(config, log, transport))
        for resp in recorder.probe(sports=args.sport, tournaments=args.tournament,
                                   matches=args.match):
            print(describe(resp))
        print(f"\n{log.frames} frames -> {log.root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
