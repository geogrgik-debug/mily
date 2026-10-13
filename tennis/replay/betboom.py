"""BetBoom raw capture to a conservative next-service-game paper replay.

Example manifest (player IDs must be verified against the rating snapshot)::

    {"12345": {"home_id": 101, "away_id": 202, "level": "chall",
               "surface": "Clay", "best_of": 3, "as_of": "2026-10-12"}}

    python -m tennis.replay.betboom CAPTURE.jsonl.gz \
        --snapshot ratings.json.gz --manifest matches.json --out replay.jsonl

Only full two-sided snapshots are quoted in v1. This prevents mixing separate
per-outcome pushes into a synthetic book. In-game 0-0 of the preceding game is
required for an early projection; the project has not calibrated a later
conditional hold used to weight that game. All skipped rows carry a reason.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from tennis.ingest.rawlog import read_raw
from tennis.market.names import GAME_WINNER, parse_market
from tennis.market.overround import normalize_proportional, shin
from tennis.model.hold import HoldParams
from tennis.ratings.snapshot import RatingsSnapshot
from tennis.replay.core import MatchReplay, Score


def _board(info) -> Score | None:
    games = {x.sequence: (x.home_score, x.away_score)
             for x in info.scoreboard.scores if x.type == 11 and x.sequence > 0}
    if not games:
        return None
    points = next(((x.home_score, x.away_score)
                   for x in info.scoreboard.scores if x.type == 12), (0, 0))
    return Score(tuple(games.get(i, (0, 0)) for i in range(1, max(games) + 1)),
                 points, info.scoreboard.serving_side)


def _objects(msg):
    kind = msg.WhichOneof('type')
    if kind == 'matches_subscribe_full':
        return ((item.match, True) for item in msg.matches_subscribe_full.full_matches)
    if kind == 'newsletters_full_match':
        return ((msg.newsletters_full_match.match, True),)
    if kind == 'newsletters_match':
        return ((msg.newsletters_match.match, False),)
    return ()


def _book(match, set_no: int, game_no: int) -> tuple[float, float] | None:
    if match.info.is_bet_stop:
        return None
    sides = {}
    for stake in match.stakes:
        ref = parse_market(stake.market_name)
        if (ref.scope, ref.set_no, ref.game_no, ref.kind) != (
                'game', set_no, game_no, GAME_WINNER):
            continue
        if stake.name in ('П1', 'П2'):
            sides[stake.name] = stake
    if set(sides) != {'П1', 'П2'}:
        return None
    if any(not s.is_live or not s.is_active or not math.isfinite(s.factor)
           or s.factor <= 1 for s in sides.values()):
        return None
    return sides['П1'].factor, sides['П2'].factor


def replay(path: str | Path, snapshot: RatingsSnapshot, manifest: dict,
           params: HoldParams, *, method: str = 'shin') -> tuple[list[dict], dict]:
    """Return completed-game rows and counts. Inputs are one ordered capture."""
    from tennis.ingest.betboom.client import load_pb

    if method not in ('shin', 'proportional'):
        raise ValueError('method must be shin or proportional')
    pb = load_pb()
    matches: dict[int, MatchReplay] = {}
    quotes: dict[tuple[int, int, int], dict] = {}
    first_skip: dict[tuple[int, int, int], str] = {}
    observed = Counter()
    last_ns = None
    for row in read_raw(path):
        if row.get('dir') != 'rx' or not isinstance(row.get('payload'), bytes):
            continue
        t = row['ts_received_ns']
        if last_ns is not None and (t < last_ns or t - last_ns > 60_000_000_000):
            # A clock step or disconnected capture makes the missing points
            # unknowable. A new file/run is replayed separately.
            for m in matches.values():
                m._invalidate('capture_clock_or_connection_gap')
        last_ns = t
        msg = pb.MainResponse()
        try:
            msg.ParseFromString(row['payload'])
        except Exception:
            observed['protobuf_parse_error'] += 1
            continue
        for obj, has_board in _objects(msg):
            mid = obj.info.id
            spec = manifest.get(str(mid))
            if spec is None:
                continue
            score = _board(obj.info)
            if score is None:
                continue
            if mid not in matches:
                prior = snapshot.prior(int(spec['home_id']), int(spec['away_id']),
                                       spec['surface'], int(spec['best_of']),
                                       spec['as_of'])
                matches[mid] = MatchReplay(
                    mid, int(spec['home_id']), int(spec['away_id']),
                    prior.p_serve_a, prior.p_serve_b, level=spec['level'],
                    surface=spec['surface'], best_of=int(spec['best_of']),
                    params=params)
            m = matches[mid]
            m.observe(score, t)
            if not has_board or m.invalid_reason:
                continue
            key = mid, score.set_no, score.game_no + 1
            if key in quotes:
                continue
            odds = _book(obj, key[1], key[2])
            if odds is None:
                continue
            p, reason = m.project_next(t)
            if p is None:
                first_skip.setdefault(key, reason)
                continue
            fair = shin(odds)[0] if method == 'shin' else normalize_proportional(odds)
            quotes[key] = {
                'quote_ts_received_ns': t, 'odds_home': odds[0],
                'odds_away': odds[1], 'model_p_home_at_quote': p,
                'market_p_home': fair[0],
                'overround': sum(1 / x for x in odds) - 1,
                'quote_age_s': None,
                'quote_age_reason': 'first_active_full_snapshot_does_not_date_price_change',
                'method': method,
                'previous_game_points_at_quote': score.points,
            }
    rows_out = []
    for mid, m in matches.items():
        observed['points_seen'] += m.points_seen
        observed['completed_games'] += len(m.outcomes)
        if m.invalid_reason:
            observed['invalid_matches'] += 1
        for (set_no, game_no), (winner, server, end_t) in sorted(m.outcomes.items()):
            f = m.forecasts.get((set_no, game_no))
            key = mid, set_no, game_no
            q = quotes.get(key)
            reason = '' if q else first_skip.get(key, 'no_active_two_sided_early_quote')
            row = {'match_id': mid, 'set_no': set_no, 'game_no': game_no,
                   'outcome_ts_received_ns': end_t, 'server': server,
                   'winner': winner, 'pregame_ts_received_ns': f.ts_received_ns if f else None,
                   'pregame_p_hold': f.p_hold if f else None,
                   'pregame_p_break': 1 - f.p_hold if f else None,
                   'skip_reason': reason, **(q or {})}
            if m.invalid_reason:
                row['match_invalid_reason'] = m.invalid_reason
            rows_out.append(row)
            observed['paired' if q else 'unpaired'] += 1
    return sorted(rows_out, key=lambda r: r['outcome_ts_received_ns']), dict(observed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('raw_log')
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--method', choices=('shin', 'proportional'), default='shin')
    args = ap.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    games, summary = replay(args.raw_log, RatingsSnapshot.load(args.snapshot),
                            manifest, HoldParams.load(), method=args.method)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8') as fh:
        for game in games:
            fh.write(json.dumps(game, ensure_ascii=False) + '\n')
    print(json.dumps({'out': str(out), 'summary': summary}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
