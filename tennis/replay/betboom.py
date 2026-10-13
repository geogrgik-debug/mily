"""BetBoom raw capture to a conservative next-service-game paper replay.

Example manifest (player IDs must be verified against the rating snapshot)::

    {"12345": {"home_id": 101, "away_id": 202, "level": "chall",
               "surface": "Clay", "best_of": 3, "as_of": "2026-10-12"}}

    python -m tennis.replay.betboom CAPTURE.jsonl.gz \
        --snapshot ratings.json.gz --manifest matches.json --out replay.jsonl

Full two-sided snapshots and pairs of per-outcome pushes within 250 ms are
quoted. A single push is never combined with an old opposite outcome. In-game
0-0 of the preceding game is required for an early projection; the project has
not calibrated a later conditional hold used to weight that game.
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

PUSH_PAIR_NS = 250_000_000


class PushPairs:
    """Assemble only two fresh outcome updates from the same score state."""

    def __init__(self, window_ns: int = PUSH_PAIR_NS):
        self.window_ns = window_ns
        self.pending: dict[tuple[int, int, int], dict[str, tuple[int, float, object]]] = {}

    def reset_match(self, mid: int) -> None:
        for key in list(self.pending):
            if key[0] == mid:
                del self.pending[key]

    def update(self, key: tuple[int, int, int], name: str, t: int,
               odd: float, active: bool, score_identity: object
               ) -> tuple[tuple[float, float], float] | None:
        if name not in ('П1', 'П2'):
            return None
        if not active or not math.isfinite(odd) or odd <= 1:
            self.pending.pop(key, None)
            return None
        pair = self.pending.setdefault(key, {})
        pair[name] = (t, odd, score_identity)
        if any(t - item[0] > self.window_ns or item[2] != score_identity
               for item in pair.values()):
            pair = self.pending[key] = {name: (t, odd, score_identity)}
        if set(pair) != {'П1', 'П2'}:
            return None
        age_s = (t - min(item[0] for item in pair.values())) / 1e9
        odds = pair['П1'][1], pair['П2'][1]
        del self.pending[key]
        return odds, age_s


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
    stake_ids: dict[tuple[int, str], tuple[int, int, str]] = {}
    push_pairs = PushPairs()
    bet_stop: dict[int, bool] = {}
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
            push_pairs.pending.clear()
        last_ns = t
        msg = pb.MainResponse()
        try:
            msg.ParseFromString(row['payload'])
        except Exception:
            observed['protobuf_parse_error'] += 1
            continue
        if msg.WhichOneof('type') == 'newsletters_stake':
            body = msg.newsletters_stake
            s = body.stake
            mid = s.match_id
            m = matches.get(mid)
            if m is None or m.invalid_reason or m.score is None or bet_stop.get(mid):
                continue
            ref = parse_market(s.market_name) if s.market_name else None
            if ref is not None and ref.scope == 'game' and ref.kind == GAME_WINNER:
                address = (ref.set_no, ref.game_no, s.name)
            else:
                address = stake_ids.get((mid, s.stake_id))
            if address is None:
                continue
            set_no, game_no, name = address
            key = mid, set_no, game_no
            score = m.score
            if key in quotes or (set_no, game_no) != (score.set_no, score.game_no + 1):
                continue
            pair = push_pairs.update(
                key, name, t, s.factor,
                body.action != pb.NEWSLETTER_ACTIONS_DELETE and s.is_active and s.is_live,
                (score.sets, score.points))
            if pair is not None:
                odds, age_s = pair
                p, reason = m.project_next(t)
                if p is None:
                    first_skip.setdefault(key, reason)
                else:
                    fair = shin(odds)[0] if method == 'shin' else normalize_proportional(odds)
                    quotes[key] = {
                        'quote_ts_received_ns': t, 'odds_home': odds[0],
                        'odds_away': odds[1], 'model_p_home_at_quote': p,
                        'market_p_home': fair[0],
                        'overround': sum(1 / x for x in odds) - 1,
                        'quote_age_s': age_s,
                        'quote_age_reason': 'older_of_two_fresh_outcome_pushes',
                        'quote_source': 'stake_pair', 'method': method,
                        'previous_game_points_at_quote': score.points,
                    }
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
            if m.score is not None and (m.score.sets, m.score.points) != (
                    score.sets, score.points):
                push_pairs.reset_match(mid)
            bet_stop[mid] = obj.info.is_bet_stop
            if bet_stop[mid]:
                push_pairs.reset_match(mid)
            m.observe(score, t)
            if not has_board or m.invalid_reason:
                continue
            for stake in obj.stakes:
                ref = parse_market(stake.market_name)
                if (ref.scope == 'game' and ref.kind == GAME_WINNER
                        and stake.stake_id):
                    stake_ids[(mid, stake.stake_id)] = (ref.set_no, ref.game_no,
                                                        stake.name)
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
                'quote_source': 'full_snapshot',
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
