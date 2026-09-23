"""The 1win recorder skeleton.

What it may do today is narrow on purpose: read its config (with no host baked
in), call the three endpoints the reconnaissance found answering, put every
answer on disk before reading it, and refuse -- with the list of what is
missing -- wherever the odds channel would be. Nothing here touches the
network: the transport is replaced, and the host below is not 1win's.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.onewin.client import (
    ENV_API_BASE,
    ENV_PARTNER_ID,
    ODDS_TODO,
    PARTNER_HEADER,
    ConfigError,
    OneWinClient,
    OneWinConfig,
    OneWinRecorder,
    main,
)
from tennis.ingest.rawlog import RawLog, find_logs, read_raw

BASE = "https://gateway.example.test/prefix"
PARTNER = "partner-under-test"
MATCH_BODY = json.dumps({"id": 31415, "service": "LIVE", "updatedAt": 1790088100123}).encode()


class FakeTransport:
    """Answers by URL fragment, and remembers every request."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, url, headers, timeout_s):
        self.calls.append((url, dict(headers), timeout_s))
        for fragment, answer in self.answers.items():
            if fragment in url:
                return answer
        return 200, {"Cache-Control": "no-cache"}, MATCH_BODY


@pytest.fixture
def log(tmp_path):
    # fsync_every=0: every frame reaches the file at once, so a test can read it
    with RawLog(tmp_path, provider="1win", clock=FakeClock(), compress=False,
                fsync_every=0) as raw:
        yield raw


def rows(root):
    return [r for path in find_logs(root) for r in read_raw(path) if r["dir"] != "meta"]


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


# --------------------------------------------------------------- known endpoints


@pytest.mark.parametrize("call,url", [
    (lambda c: c.sport(33), f"{BASE}/sports/get?sportId=33"),
    (lambda c: c.tournament(7788), f"{BASE}/tournaments/get?tournamentId=7788"),
    (lambda c: c.match(31415), f"{BASE}/matches/get?matchId=31415"),
])
def test_the_known_endpoints(log, call, url):
    transport = FakeTransport()
    client = OneWinClient(OneWinConfig(BASE, PARTNER), log, transport)

    resp = call(client)

    ((sent_url, headers, _),) = transport.calls
    assert sent_url == resp.url == url
    assert headers[PARTNER_HEADER] == PARTNER


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


def test_the_partner_id_is_sent_but_not_logged(log, tmp_path):
    OneWinClient(OneWinConfig(BASE, PARTNER), log, FakeTransport()).match(31415)
    log.close()
    for path in find_logs(tmp_path):
        assert PARTNER not in path.read_text(encoding="utf-8")


# --------------------------------------------------------------- the odds, not yet


def test_the_odds_channel_refuses_to_guess(log):
    client = OneWinClient(OneWinConfig(BASE, PARTNER), log, FakeTransport())

    with pytest.raises(NotImplementedError) as err:
        client.odds_channel(31415)
    with pytest.raises(NotImplementedError):
        OneWinRecorder(client).run()

    assert "Request URL" in str(err.value) and "Response" in str(err.value)


def test_the_command_says_what_is_missing_instead_of_recording(capsys):
    assert main([]) == 2
    assert ODDS_TODO in capsys.readouterr().err


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
