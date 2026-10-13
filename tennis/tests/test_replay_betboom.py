"""Feed bridge contract with synthetic protobuf frames; no capture file needed."""
import json
import math
import sys
import types

from tennis.model.hold import HoldParams
from tennis.replay import betboom


def _obj(value):
    if isinstance(value, dict):
        return types.SimpleNamespace(**{k: _obj(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_obj(x) for x in value]
    return value


class FakeResponse:
    def ParseFromString(self, payload):
        data = json.loads(payload)
        self.kind = data['kind']
        setattr(self, self.kind, _obj(data['body']))

    def WhichOneof(self, _):
        return self.kind


def _frame(t, games, points, side, *, active=True):
    stake = lambda name, odd: dict(market_name='1-й сет 2-й гейм: Исход',
                                   name=name, factor=odd, is_live=True,
                                   is_active=active, stake_id=name)
    match = {'info': {
        'id': 12345, 'is_bet_stop': False,
        'scoreboard': {'serving_side': side, 'scores': [
            {'type': 11, 'sequence': 1, 'home_score': games[0], 'away_score': games[1]},
            {'type': 12, 'sequence': 1, 'home_score': points[0], 'away_score': points[1]},
        ]}}, 'stakes': [stake('П1', 2.5), stake('П2', 1.4)]}
    return {'dir': 'rx', 'ts_received_ns': t,
            'payload': json.dumps({'kind': 'newsletters_full_match',
                                   'body': {'match': match}}).encode()}


def _push(t, side, odd, active=True):
    return {'dir': 'rx', 'ts_received_ns': t,
            'payload': json.dumps({'kind': 'newsletters_stake', 'body': {
                'action': 2, 'stake': {'match_id': 12345, 'stake_id': side,
                'market_name': '1-й сет 2-й гейм: Исход', 'name': side,
                'factor': odd, 'is_active': active, 'is_live': True}}}).encode()}


def test_full_snapshot_replay_and_first_early_quote(monkeypatch):
    fake_client = types.ModuleType('tennis.ingest.betboom.client')
    fake_client.load_pb = lambda: types.SimpleNamespace(
        MainResponse=FakeResponse, NEWSLETTER_ACTIONS_DELETE=3)
    monkeypatch.setitem(sys.modules, 'tennis.ingest.betboom.client', fake_client)
    rows = [_frame(1, (0, 0), (0, 0), 1)]
    rows += [_frame(i, (0, 0), (score, 0), 1)
             for i, score in enumerate((15, 30, 40), 2)]
    rows += [_frame(5, (1, 0), (0, 0), 2)]
    rows += [_frame(i, (1, 0), (0, score), 2)
             for i, score in enumerate((15, 30, 40), 6)]
    rows += [_frame(9, (1, 1), (0, 0), 1)]
    monkeypatch.setattr(betboom, 'read_raw', lambda _: rows)
    prior = types.SimpleNamespace(p_serve_a=.61, p_serve_b=.57)
    snapshot = types.SimpleNamespace(prior=lambda *args: prior)
    manifest = {'12345': dict(home_id=101, away_id=202, surface='Clay',
                               best_of=3, as_of='2026-10-12', level='chall')}
    games, stats = betboom.replay('unused', snapshot, manifest,
                                  HoldParams.load(), method='proportional')
    assert stats == {'points_seen': 8, 'completed_games': 2,
                     'unpaired': 1, 'paired': 1}
    assert games[0]['skip_reason'] == 'no_active_two_sided_early_quote'
    assert games[1]['winner'] == 2 and games[1]['server'] == 2
    assert games[1]['quote_ts_received_ns'] == 1
    assert math.isclose(games[1]['market_p_home'], (1 / 2.5) / (1 / 2.5 + 1 / 1.4))
    assert 0 < games[1]['model_p_home_at_quote'] < 1
    assert games[1]['quote_age_s'] is None


def test_suspended_pair_is_not_a_quote():
    frame = _frame(1, (0, 0), (0, 0), 1, active=False)
    obj = _obj(json.loads(frame['payload'])['body']['match'])
    assert betboom._book(obj, 1, 2) is None


def test_push_pair_requires_both_fresh_outcomes():
    pairs = betboom.PushPairs(window_ns=250_000_000)
    key = (12345, 1, 2)
    assert pairs.update(key, 'П1', 1, 2.5, True, '0-0') is None
    assert pairs.update(key, 'П2', 300_000_002, 1.4, True, '0-0') is None
    assert pairs.update(key, 'П1', 300_000_003, 2.5, True, '0-0') == (
        (2.5, 1.4), 1e-9)
    assert pairs.update(key, 'П1', 5, 2.5, True, '0-0') is None
    assert pairs.update(key, 'П2', 6, 1.4, True, '15-0') is None
    assert pairs.update(key, 'П1', 7, 2.5, False, '15-0') is None


def test_push_pair_precedes_full_snapshot(monkeypatch):
    fake_client = types.ModuleType('tennis.ingest.betboom.client')
    fake_client.load_pb = lambda: types.SimpleNamespace(
        MainResponse=FakeResponse, NEWSLETTER_ACTIONS_DELETE=3)
    monkeypatch.setitem(sys.modules, 'tennis.ingest.betboom.client', fake_client)
    rows = [_frame(1, (0, 0), (0, 0), 1, active=False),
            _push(2, 'П1', 2.5), _push(3, 'П2', 1.4),
            _frame(4, (0, 0), (0, 0), 1)]
    rows += [_frame(i, (0, 0), (score, 0), 1)
             for i, score in enumerate((15, 30, 40), 5)]
    rows += [_frame(8, (1, 0), (0, 0), 2)]
    rows += [_frame(i, (1, 0), (0, score), 2)
             for i, score in enumerate((15, 30, 40), 9)]
    rows += [_frame(12, (1, 1), (0, 0), 1)]
    monkeypatch.setattr(betboom, 'read_raw', lambda _: rows)
    prior = types.SimpleNamespace(p_serve_a=.61, p_serve_b=.57)
    snap = types.SimpleNamespace(prior=lambda *args: prior)
    manifest = {'12345': dict(home_id=101, away_id=202, surface='Clay',
                               best_of=3, as_of='2026-10-12', level='chall')}
    games, stats = betboom.replay('unused', snap, manifest, HoldParams.load())
    assert stats['paired'] == 1
    assert games[0]['skip_reason'] == 'no_active_two_sided_early_quote'
    assert games[1]['quote_ts_received_ns'] == 3
    assert games[1]['quote_source'] == 'stake_pair'
    assert games[1]['quote_age_s'] == 1e-9
