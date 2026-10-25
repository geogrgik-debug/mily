"""1win's push frames to quotes, keyed the way BetBoom's are.

The frames are shaped after the ones recorded live on 23.09 (a WTA 125 match,
Timofeeva against Rinaldo Persson): a snapshot names every group and odds
item; an update carries the item's id, odds, status and server time, and
names only what is new. The set winner's name came with a Latin "c" in "cет".
"""
import json

from tennis.market.streams import fold, onewin_quotes

MID = 40403794
T0 = 1_790_175_800_000_000_000
BOOT = 1_789_742_505_831_000_000


def frame(t, message_type, data):
    wall = T0 + round(t * 1e9)
    text = "42" + json.dumps(["u", {"data": data, "messageType": message_type}, "Q3E"],
                             ensure_ascii=False)
    return {"dir": "rx", "channel": "push", "payload": text,
            "ts_received_ns": wall, "ts_mono_ns": wall - BOOT}


def item(oid, cf, outcome=None, *, status=1, name=None, vars=None):
    out = {"id": oid, "ts": 1790175815676, "status": status}
    if cf is not None:
        out["cf"] = cf
    for key, value in (("outcome", outcome), ("name", name), ("vars", vars)):
        if value is not None:
            out[key] = value
    return out


def group(gid, odds, name=None):
    out = {"id": gid, "isBase": False, "order": 0, "renderType": "cols-2", "oddsList": odds}
    if name is not None:
        out["name"] = name
    return out


GAME_7 = {"v1": "1", "v2": "7"}


def snapshot(t=0.0):
    return frame(t, "match-odds-snapshot", {"matchId": MID, "isBaseOddsGroups": False, "oddsGroups": [
        group("15813", [item("m1", 1.05, "1", name="Мария Тимофеева"),
                        item("m2", 8.79, "2", name="Кайса Риналдо Перссон")], "Победитель"),
        group("16724", [item("s1", 12.6, "1"), item("s2", 1.01, "2")], "1-й cет. Победитель"),
        group("82646", [item("g1", 2.88, "1", vars=GAME_7), item("g2", 1.37, "2", vars=GAME_7)],
              "Победитель гейма"),
        group("82644", [item("y", 1.89, "yes", vars=GAME_7), item("n", 1.78, "no", vars=GAME_7)],
              "Будет ли 40-40"),
        group("15815", [item("u", 1.87, "under", vars={"v1": "18.5"}),
                        item("o", 1.87, "over", vars={"v1": "18.5"})], "Тотал"),
    ]})


def update(t, *groups):
    return frame(t, "match-odds", {"matchId": MID, "oddsGroups": list(groups), "ts": 1790175818973})


def seen(quotes):
    return [(q.market, q.outcome, q.odds, q.active) for q in quotes if q is not None]


def test_winner_markets_get_betboom_addresses_and_the_rest_is_left_out():
    got = seen(onewin_quotes([snapshot()]))

    assert got == [(("match", None, None, "Исход"), "1", 1.05, True),
                   (("match", None, None, "Исход"), "2", 8.79, True),
                   (("set", 1, None, "Исход"), "1", 12.6, True),
                   (("set", 1, None, "Исход"), "2", 1.01, True),
                   (("game", 1, 7, "Исход"), "1", 2.88, True),
                   (("game", 1, 7, "Исход"), "2", 1.37, True)]
    assert {q.source for q in onewin_quotes([snapshot()])} == {"push"}


def test_an_update_is_known_by_its_odds_id_and_a_suspension_keeps_the_odds():
    rows = [snapshot(),
            update(3, group("82646", [item("g1", 2.75), item("g2", 1.42)])),
            update(5, group("82646", [item("g1", None, status=2), item("g2", None, status=2)]))]

    assert seen(onewin_quotes(rows))[6:] == [
        (("game", 1, 7, "Исход"), "1", 2.75, True), (("game", 1, 7, "Исход"), "2", 1.42, True),
        (("game", 1, 7, "Исход"), "1", 2.75, False), (("game", 1, 7, "Исход"), "2", 1.42, False)]


def test_a_new_market_is_named_by_the_update_that_brings_it():
    game_8 = {"v1": "1", "v2": "8"}
    rows = [snapshot(), update(9, group("82646", [item("h1", 1.6, "1", vars=game_8),
                                                  item("h2", 2.2, "2", vars=game_8)],
                                        "Победитель гейма"))]
    assert seen(onewin_quotes(rows))[6:] == [(("game", 1, 8, "Исход"), "1", 1.6, True),
                                             (("game", 1, 8, "Исход"), "2", 2.2, True)]


def test_an_item_missing_from_a_later_snapshot_is_gone():
    later = snapshot(30)
    later_data = json.loads(later["payload"][2:])[1]["data"]
    later_data["oddsGroups"] = [g for g in later_data["oddsGroups"] if g["id"] != "82646"]
    later["payload"] = "42" + json.dumps(["u", {"data": later_data,
                                                "messageType": "match-odds-snapshot"}, "Q"],
                                         ensure_ascii=False)

    got = seen(onewin_quotes([snapshot(), later]))

    assert (("game", 1, 7, "Исход"), "1", None, False) in got[6:]


def test_a_break_forgets_everything():
    rows = [snapshot(), None, update(3, group("82646", [item("g1", 2.75), item("g2", 1.42)]))]
    quotes = list(onewin_quotes(rows))
    assert quotes[6] is None and len(quotes) == 7


def test_pings_opens_and_what_was_sent_are_not_quotes():
    ping = dict(snapshot(), payload="2")
    sent = dict(snapshot(), dir="tx")
    assert list(onewin_quotes([ping, sent])) == []


def test_the_players_come_from_the_live_list():
    body = {"result": {"items": [{"id": MID, "sportId": 33, "competitors": [
        {"position": 2, "name": "Кайса Риналдо Перссон", "slug": "kajsa-rinaldo-persson"},
        {"position": 1, "name": "Мария Тимофеева", "slug": "maria-timofeeva"}]}]}}
    row = {"dir": "rx", "channel": "matches/get-many",
           "payload": json.dumps(body, ensure_ascii=False).encode(),
           "ts_received_ns": T0, "ts_mono_ns": T0 - BOOT}
    players = {}

    assert list(onewin_quotes([row], players)) == []
    assert players == {MID: ("Мария Тимофеева", "Кайса Риналдо Перссон")}


def test_a_game_book_folds_into_one_move_per_repricing():
    rows = [snapshot(), update(3, group("82646", [item("g1", 2.75), item("g2", 1.42)]))]

    moves = [e for e in fold(onewin_quotes(rows)) if e.is_move]

    assert {e.market for e in moves} == {("game", 1, 7, "Исход")}
    assert {e.ts_received_ns for e in moves} == {T0 + 3_000_000_000}
