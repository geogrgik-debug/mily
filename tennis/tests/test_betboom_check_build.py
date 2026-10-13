"""check_build answers two questions from the public widget files -- has the
socket's uuid moved, has the schema -- and has to answer them offline here,
without taking a rebuilt minifier for a new schema."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.betboom import check_build as cb
from tennis.ingest.betboom.extract_schema import main as extract_main, schema_of

UUID = "01a0d23a-f305-719c-bb3d-11c5bed388f1"
OURS = "wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1?uuid=" + UUID

# The shapes below are cut from the 25.09 widget (build 8.45.2-f9e91ba9).
RUNTIME_ENV = ('const _={"STORYLY_TOKEN":"not-read","FEED_WS_PROTOCOL":"protobuf",'
               '"FEED_WS_URL_TEMPLATE":"wss://{sporthubPartnerName}-ws2.sporthub.bet:443'
               '/api/tree_ws/v1?uuid=' + UUID + '","APP_BUILD":"8.45.2-f9e91ba9"};')
WIDGET_JS = ('import("./App-CO-7jV8h.js");const d=["./vendor-DB0K_Foj.js",'
             '"./vendor-core-Doaqx-Vu.js","./vendor-ui-B-1Ddfro.js"];')


def bundle(factor_no=10, live=1, any_id="cn", enum_id="OR"):
    return (
        f'{any_id}=new class extends z{{constructor(){{super("google.protobuf.Any",['
        '{no:1,name:"type_url",kind:"scalar",T:9},{no:2,name:"value",kind:"scalar",T:12}])}};'
        'st=new class extends z{constructor(){super("bb.sport_ws.v1.models.ModelsStake",['
        '{no:1,name:"stake_id",kind:"scalar",T:9},'
        f'{{no:{factor_no},name:"factor",kind:"scalar",T:1}},'
        f'{{no:20,name:"extra",kind:"message",T:()=>{any_id}}},'
        '{no:3,name:"type",kind:"enum",T:()=>["bb.sport_ws.v1.common.MatchTypes",'
        f'{enum_id},"MATCH_TYPES_"]}}])}}}};'
        f'{enum_id}=(function(e){{return e[e.UNSPECIFIED=0]="UNSPECIFIED",'
        f'e[e.LIVE={live}]="LIVE",e[e.PREMATCH=2]="PREMATCH",e}})({{}});'
    )


def site(src=None, runtime=RUNTIME_ENV):
    files = {cb.WIDGET + "runtime-env.js": runtime, cb.WIDGET + "widget.js": WIDGET_JS,
             cb.WIDGET + "vendor-core-Doaqx-Vu.js": bundle() if src is None else src}
    return lambda url: files[url]


def committed(tmp_path):
    """The schema.json extract_schema writes for today's bundle, as proto/ holds it."""
    src = tmp_path / "bundle.js"
    src.write_text(bundle(), encoding="utf-8")
    assert extract_main([str(src), "-o", str(tmp_path / "proto"), "--packages"]) == 0
    return tmp_path / "proto" / "schema.json"


def test_runtime_env_gives_the_build_and_the_socket_uuid():
    assert cb.runtime_env(RUNTIME_ENV) == {"app_build": "8.45.2-f9e91ba9", "uuid": UUID}


def test_a_template_without_a_uuid_reads_as_none():
    text = '{"FEED_WS_URL_TEMPLATE":"wss://x/api/tree_ws/v1","APP_BUILD":"8.45.1-5d29aa45"}'
    assert cb.runtime_env(text)["uuid"] is None


def test_a_runtime_env_of_another_shape_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        cb.runtime_env('{"FEED_WS_PROTOCOL":"protobuf"}')


def test_the_bundle_is_the_vendor_core_widget_js_names():
    assert cb.bundle_name(WIDGET_JS) == "vendor-core-Doaqx-Vu.js"
    with pytest.raises(ValueError):
        cb.bundle_name('import("./App-CO-7jV8h.js")')


def test_the_live_bundle_is_read_as_extract_schema_writes_it(tmp_path):
    written = json.loads(committed(tmp_path).read_text(encoding="utf-8"))
    assert written == json.loads(json.dumps(schema_of(bundle())))


def test_the_same_widget_passes(tmp_path):
    ok, lines = cb.check(fetch=site(), schema_path=committed(tmp_path), url=OURS)
    assert ok, lines
    assert lines[0] == "сборка виджета: 8.45.2-f9e91ba9"
    assert any("uuid сокета совпадает" in s for s in lines)
    assert any("совпадает с proto/schema.json" in s for s in lines)


def test_a_moved_uuid_fails_and_names_both():
    """24.09: the feed began to refuse sockets without the widget's uuid, and
    the capture stood 29 hours before anyone read runtime-env.js."""
    other = "wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1?uuid=0000"
    ok, lines = cb.check(fetch=site(), schema_path=cb.SCHEMA, url=other)
    assert not ok
    assert any("uuid сокета сменился" in s and UUID in s and "0000" in s for s in lines)


def test_a_renumbered_field_fails_by_name(tmp_path):
    ok, lines = cb.check(fetch=site(bundle(factor_no=11)),
                         schema_path=committed(tmp_path), url=OURS)
    assert not ok
    text = "\n".join(lines)
    assert "ModelsStake, поле 10" in text and "ModelsStake, поле 11" in text


def test_a_renumbered_enum_value_fails(tmp_path):
    ok, lines = cb.check(fetch=site(bundle(live=3)),
                         schema_path=committed(tmp_path), url=OURS)
    assert not ok
    assert any("перечисление bb.sport_ws.v1.common.MatchTypes" in s for s in lines)


def test_a_rebuilt_minifier_alone_is_not_a_new_schema(tmp_path):
    """Every build renames the minified idents; the wire stays the same."""
    ok, lines = cb.check(fetch=site(bundle(any_id="Qx", enum_id="Zz")),
                         schema_path=committed(tmp_path), url=OURS)
    assert ok, lines


def test_a_bundle_with_no_messages_cannot_be_checked(tmp_path):
    with pytest.raises(ValueError):
        cb.check(fetch=site("var nothing=1;"), schema_path=committed(tmp_path), url=OURS)


def test_the_committed_schema_compares_clean_with_itself():
    """Every field shape in the real 223-message schema goes through."""
    real = json.loads(cb.SCHEMA.read_text(encoding="utf-8"))
    assert len(real["messages"]) > 200
    assert cb.schema_changes(real, real) == []


@pytest.mark.parametrize("outcome,code", [
    ((True, ["ok"]), 0), ((False, ["moved"]), 1), (OSError("no network"), 2),
])
def test_the_exit_code_says_which(monkeypatch, outcome, code):
    def fake_check():
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(cb, "check", fake_check)
    assert cb.main([]) == code
