"""The 1win recorder: config, the REST gateway, and the push socket.

Nothing here touches the network: the transport and the socket are replaced,
and the host below is not 1win's. The shapes of the frames are the ones seen
live on 23.09 (a match page, then plain Python against the push server).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.onewin.client import (
    ENV_API_BASE,
    ENV_PARTNER_ID,
    PARTNER_HEADER,
    USER_AGENT,
    ConfigError,
    OneWinClient,
    OneWinConfig,
    OneWinRecorder,
    main,
    singles,
)
from tennis.ingest.rawlog import RawLog, find_logs, read_raw

BASE = "https://gateway.example.test/prefix"
PARTNER = "partner-under-test"
MATCH_BODY = json.dumps({"id": 31415, "service": "LIVE", "updatedAt": 1790088100123}).encode()


def player(position, name):
    return {"position": position, "name": name, "slug": name.lower().replace(" ", "-")}


def live_item(mid, a="Мария Тимофеева", b="Кайса Риналдо Перссон", *, sport=33,
              category="wta-125k"):
    return {"id": mid, "sportId": sport, "slug": f"m-{mid}", "name": f"{a} - {b}",
            "category": {"id": 1, "slug": category},
            "competitors": [player(1, a), player(2, b)]}


def live_body(*items):
    return json.dumps({"result": {"items": list(items), "meta": {"total": len(items)}}},
                      ensure_ascii=False).encode()


class FakeTransport:
    """Answers by URL fragment, and remembers every request."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, url, headers, timeout_s, data=None):
        self.calls.append((url, dict(headers), timeout_s, data))
        for fragment, answer in self.answers.items():
            if fragment in url:
                return answer
        return 200, {"Cache-Control": "no-cache"}, MATCH_BODY


class FakeSocket:
    """Plays a script of frames, remembers what is sent, then drops."""

    def __init__(self, frames, *, linger=0.3):
        self.frames = list(frames)
        self.sent = []
        self.linger = linger

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(self.linger)      # time for the subscriptions to go out
        raise ConnectionError("script over")

    async def send(self, text):
        self.sent.append(text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


OPEN = '0{"sid":"a","upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":1000000}'
CONNECTED = '40{"sid":"b","pid":"c"}'
ODDS = ('42["u",{"data":{"matchId":1,"oddsGroups":[],"ts":1790175514508,'
        '"isBaseOddsGroups":false},"messageType":"match-odds-snapshot"},"Q3EnfgW"]')


@pytest.fixture
def log(tmp_path):
    # fsync_every=0: every frame reaches the file at once, so a test can read it
    with RawLog(tmp_path, provider="1win", clock=FakeClock(), compress=False,
                fsync_every=0) as raw:
        yield raw


def rows(root):
    return [r for path in find_logs(root) for r in read_raw(path) if r["dir"] != "meta"]


def recorder(log, *items, max_matches=20):
    transport = FakeTransport({"matches/get-many": (200, {}, live_body(*items))})
    return OneWinRecorder(OneWinClient(OneWinConfig(BASE, PARTNER), log, transport),
                          max_matches=max_matches), transport


def subscriptions(sent):
    out = []
    for text in sent:
        if text.startswith("42"):
            kind, body = json.loads(text[2:])
            out.append((body["messageType"], body["data"]["matchIds"]))
    return out


# --------------------------------------------------------------- config


def test_there_is_no_default_host_or_partner():
    """The site's mirror already rotated once; a stale host in the code would
    fail quietly, which is the one way a recorder must not fail."""
    with pytest.raises(ConfigError) as err:
        OneWinConfig.load(env={})
    assert ENV_API_BASE in str(err.value) and ENV_PARTNER_ID in str(err.value)


def test_config_comes_from_the_environment_and_flags_win():
    env = {ENV_API_BASE: "https://from-env.example.test/p/", ENV_PARTNER_ID: "env-partner"}

    from_env = OneWinConfig.load(env=env)
    from_flags = OneWinConfig.load(env=env, api_base=BASE, partner_id="flag-partner")

    assert (from_env.api_base, from_env.partner_id) == (
        "https://from-env.example.test/p", "env-partner")
    assert (from_flags.api_base, from_flags.partner_id) == (BASE, "flag-partner")


@pytest.mark.parametrize("base", [
    "http://gateway.example.test/prefix",          # not TLS
    "https://gateway.example.test/prefix?x=1",     # a query belongs to a request
    "gateway.example.test/prefix",                 # no scheme
])
def test_a_malformed_base_is_refused(base):
    with pytest.raises(ConfigError, match=ENV_API_BASE):
        OneWinConfig.load(env={}, api_base=base, partner_id=PARTNER)


def test_the_push_socket_is_on_the_same_gateway():
    assert OneWinConfig(BASE, PARTNER).push_url() == (
        "wss://gateway.example.test/prefix/push-server-v2/"
        f"?Language=ru&externalPartnerId={PARTNER}&EIO=4&transport=websocket")


# --------------------------------------------------------------- REST


@pytest.mark.parametrize("call,url", [
    (lambda c: c.sport(33), f"{BASE}/sports/get?sportId=33"),
    (lambda c: c.tournament(7788), f"{BASE}/tournaments/get?tournamentId=7788"),
    (lambda c: c.match(31415), f"{BASE}/matches/get?matchId=31415"),
])
def test_the_known_endpoints(log, call, url):
    transport = FakeTransport()
    client = OneWinClient(OneWinConfig(BASE, PARTNER), log, transport)

    resp = call(client)

    ((sent_url, headers, _, data),) = transport.calls
    assert sent_url == resp.url == url and data is None
    assert headers[PARTNER_HEADER] == PARTNER


def test_the_gateway_is_asked_as_a_browser_in_russian(log):
    """Without a browser's User-Agent the gateway answers 403; `x-lang: ru`
    gives Russian names beside the Latin slugs, and BetBoom's are Russian."""
    transport = FakeTransport()
    OneWinClient(OneWinConfig(BASE, PARTNER), log, transport).sport(33)
    headers = transport.calls[0][1]
    assert headers["User-Agent"] == USER_AGENT and headers["x-lang"] == "ru"


def test_the_live_list_is_asked_for_tennis(log, tmp_path):
    transport = FakeTransport({"matches/get-many": (200, {}, live_body(live_item(7)))})
    OneWinClient(OneWinConfig(BASE, PARTNER), log, transport).live_tennis()

    ((url, headers, _, data),) = transport.calls
    assert url == f"{BASE}/matches/get-many"
    assert json.loads(data) == {"limit": 100, "service": "live", "sportIds": [33]}
    tx, rx = rows(tmp_path)
    assert json.loads(tx["payload"])["sportIds"] == [33]
    assert rx["channel"] == "matches/get-many" and rx["meta"]["status"] == 200


def test_every_answer_is_on_disk_before_it_is_read(log, tmp_path):
    """404 was itself a finding of the reconnaissance: it goes in the log with
    its status, and an unreadable body is kept verbatim, not dropped."""
    transport = FakeTransport({"markets/get": (404, {}, b"<html>Not Found</html>")})
    client = OneWinClient(OneWinConfig(BASE, PARTNER), log, transport)

    resp = client.get("markets/get", matchId=31415)
    with pytest.raises(ValueError):
        resp.json()

    tx, rx = rows(tmp_path)
    assert (tx["dir"], tx["payload"]) == ("tx", f"{BASE}/markets/get?matchId=31415")
    assert rx["payload"] == b"<html>Not Found</html>"
    assert rx["meta"]["status"] == 404 and rx["channel"] == "markets/get"


def test_the_cache_header_is_kept(log, tmp_path):
    """`tournaments/get` came with max-age=600: whether an endpoint is live is
    read off the answer, so the answer's caching is recorded with it."""
    transport = FakeTransport({"tournaments/get": (200, {"Cache-Control": "max-age=600"}, b"{}")})
    resp = OneWinClient(OneWinConfig(BASE, PARTNER), log, transport).tournament(7788)

    assert resp.cache_control == "max-age=600"
    assert rows(tmp_path)[-1]["meta"]["cache_control"] == "max-age=600"


# --------------------------------------------------------------- which matches


def test_only_live_tennis_singles_take_a_slot():
    items = [live_item(1),
             live_item(2, "А. Данилина/З. Кулумбаева", "К. Букса/С. Сорибес Тормо"),
             live_item(3, "Andre Agassi (Smet13)", "Andy Murray (Abbat)", category="cybertennis"),
             live_item(4, sport=18),
             {"id": 5, "sportId": 33, "competitors": [player(1, "Only One")]}]
    assert singles(items) == [1]


def test_a_failed_live_ask_keeps_the_last_list(log):
    rec, transport = recorder(log, live_item(1), live_item(2))
    assert rec.discover() == [1, 2]

    transport.answers["matches/get-many"] = (500, {}, b"<html>busy</html>")

    assert rec.discover() == [1, 2]
    assert rec.discover_errors == 1


# --------------------------------------------------------------- the push socket


def test_a_session_connects_subscribes_answers_pings_and_logs_every_frame(log, tmp_path):
    rec, _ = recorder(log, live_item(1), live_item(2))
    ws = FakeSocket([OPEN, CONNECTED, "2", ODDS])

    with pytest.raises(ConnectionError, match="script over"):
        asyncio.run(rec._session(ws))

    assert ws.sent[:2] == ["40", "3"]
    assert subscriptions(ws.sent) == [("subscribe-match-info", [1, 2]),
                                      ("subscribe-match-odds", [1, 2])]
    assert '"isBaseOddsGroups":false' in ws.sent[-1]
    received = [r["payload"] for r in rows(tmp_path) if r["dir"] == "rx" and r["channel"] == "push"]
    assert received == [OPEN, CONNECTED, "2", ODDS]
    assert rec.kinds["match-odds-snapshot"] == 1


def test_subscriptions_wait_for_the_namespace_and_stop_at_the_limit(log):
    rec, _ = recorder(log, live_item(1), live_item(2), live_item(3), max_matches=2)
    ws = FakeSocket([OPEN])            # never connected: nothing may be asked

    with pytest.raises(ConnectionError):
        asyncio.run(rec._session(ws))
    assert subscriptions(ws.sent) == []

    ws = FakeSocket([OPEN, CONNECTED])
    with pytest.raises(ConnectionError):
        asyncio.run(rec._session(ws))
    assert subscriptions(ws.sent)[0] == ("subscribe-match-info", [1, 2])


def test_a_silent_socket_ends_the_session(log):
    class Silent(FakeSocket):
        async def recv(self):
            await asyncio.sleep(10)

    rec, _ = recorder(log)
    with pytest.raises(ConnectionError, match="no frame"):
        asyncio.run(rec._session(Silent([]), silence_s=0.05))


def test_a_refused_namespace_ends_the_session(log):
    rec, _ = recorder(log)
    with pytest.raises(ConnectionError, match="ended the session"):
        asyncio.run(rec._session(FakeSocket([OPEN, '44{"message":"no"}'])))


def test_a_reconnect_writes_a_break_and_asks_for_everything_again(log, tmp_path):
    rec, _ = recorder(log, live_item(1))
    sockets = [FakeSocket([OPEN, CONNECTED]), FakeSocket([OPEN, CONNECTED])]
    waits = []

    class Stop(Exception):
        pass

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise Stop

    rec._sleep = sleep
    with pytest.raises(Stop):
        asyncio.run(rec.run(connect=lambda: sockets.pop(0)))

    breaks =[r for p in find_logs(tmp_path) for r in read_raw(p) if r["channel"] == "_conn"]
    assert len(breaks) == 2 and rec.reconnects == 2
    asked = [s for r in rows(tmp_path) if r["dir"] == "tx" and r["channel"] == "push"
             for s in [r["payload"]] if "subscribe-match-odds" in s]
    assert len(asked) == 2                  # once per socket


def test_the_partner_id_is_sent_but_never_logged(log, tmp_path):
    """Found in review: a refusal that echoed the socket's address went to the
    log verbatim, before the error's text was cleaned."""
    OneWinClient(OneWinConfig(BASE, PARTNER), log, FakeTransport()).match(31415)
    echo = FakeSocket([OPEN, f'44{{"message":"unknown externalPartnerId={PARTNER}"}}'])
    with pytest.raises(ConnectionError):
        asyncio.run(recorder(log)[0]._session(echo))

    rec, _ = recorder(log)

    class Stop(Exception):
        pass

    def refused():
        raise ConnectionError(f"HTTP 403 for {OneWinConfig(BASE, PARTNER).push_url()}")

    async def stop(_):
        raise Stop

    rec._sleep = stop
    with pytest.raises(Stop):
        asyncio.run(rec.run(connect=refused))
    log.close()
    for path in find_logs(tmp_path):
        text = path.read_text(encoding="utf-8")
        assert PARTNER not in text
    assert "<partner id>" in rec.last_disconnect


# --------------------------------------------------------------- command line


def test_the_command_says_what_is_missing_instead_of_recording(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(ENV_API_BASE, raising=False)
    monkeypatch.delenv(ENV_PARTNER_ID, raising=False)
    assert main(["--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert ENV_API_BASE in err and ENV_PARTNER_ID in err
    assert find_logs(tmp_path) == []


def test_probe_writes_every_answer_and_prints_its_keys(tmp_path, capsys):
    transport = FakeTransport()
    code = main(["--probe", "--match", "31415", "--sport", "33", "--out", str(tmp_path),
                 "--api-base", BASE, "--partner-id", PARTNER], transport=transport)

    assert code == 0
    assert len(transport.calls) == 2
    assert [r["dir"] for r in rows(tmp_path)] == ["tx", "rx", "tx", "rx"]
    assert all("provider=1win" in str(p) for p in find_logs(tmp_path))
    assert "keys: id, service, updatedAt" in capsys.readouterr().out


def test_probe_without_config_fails_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(ENV_API_BASE, raising=False)
    monkeypatch.delenv(ENV_PARTNER_ID, raising=False)
    transport = FakeTransport()

    assert main(["--probe", "--match", "1", "--out", str(tmp_path)], transport=transport) == 2
    assert transport.calls == []
    assert ENV_API_BASE in capsys.readouterr().err
