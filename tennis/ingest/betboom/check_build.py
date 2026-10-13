"""Is the BetBoom feed still speaking the schema and address the recorder uses?

Two things on the sportbook widget change without notice, and each has cost
data or could:

* The protobuf field numbers are a contract. A new build (APP_BUILD in the
  widget's runtime-env.js) may renumber them, and the recording goes wrong
  without a single error. On 25.09 build 8.45.2-f9e91ba9 was out; its schema
  proved byte-identical to 8.45.1-5d29aa45's, but that took a manual run.
* The socket address. From 24.09 15:30 MSK the feed refused every socket
  without the widget's ?uuid= (3003 "Access rejected"), and the capture stood
  29 hours until a8d2f61 put the uuid into DEFAULT_URL.

This reads the public widget files -- no socket, no account -- and compares
the uuid in FEED_WS_URL_TEMPLATE with the recorder's DEFAULT_URL, and the
schema parsed from the vendor-core bundle that widget.js names with
proto/schema.json, field by field. Minified identifiers are resolved first:
they differ between builds even when nothing on the wire does.

    python -m tennis.ingest.betboom.check_build

Exit 0: same schema and uuid. 1: something moved, and the lines say what.
2: could not check (network, or the widget files changed shape).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from tennis.ingest.betboom.client import DEFAULT_URL, USER_AGENT
from tennis.ingest.betboom.extract_schema import schema_of

WIDGET = "https://sportbook.sporthub.bet/widgets/sportbook/v1/modern/"
SCHEMA = Path(__file__).parent / "proto" / "schema.json"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def uuid_of(url: str) -> str | None:
    m = re.search(r"[?&]uuid=([0-9A-Za-z-]+)", url)
    return m.group(1) if m else None


def runtime_env(text: str) -> dict:
    """APP_BUILD and the socket uuid from runtime-env.js; nothing else is read,
    since the file also carries third-party tokens."""
    build = re.search(r'"APP_BUILD"\s*:\s*"([^"]+)"', text)
    template = re.search(r'"FEED_WS_URL_TEMPLATE"\s*:\s*"([^"]+)"', text)
    if not build or not template:
        raise ValueError("runtime-env.js has no APP_BUILD or FEED_WS_URL_TEMPLATE")
    return {"app_build": build.group(1), "uuid": uuid_of(template.group(1))}


def bundle_name(widget_js: str) -> str:
    """The content-hashed vendor-core bundle widget.js loads; the name changes
    with every deploy, and the protobuf-es message literals live in it."""
    m = re.search(r"\./(vendor-core-[\w-]+\.js)", widget_js)
    if not m:
        raise ValueError("widget.js names no vendor-core bundle")
    return m.group(1)


def comparable(schema: dict) -> dict:
    """Messages and enums with the minified idents resolved to type names."""
    idents = schema.get("idents", {})
    messages = {
        name: {f["no"]: (f["name"], f["kind"], f["repeated"], f["optional"],
                         f.get("oneof"), f.get("scalar"),
                         idents.get(f.get("type_ref"), f.get("type_ref")))
               for f in fields}
        for name, fields in schema["messages"].items()}
    enums = {name: [tuple(v) for v in values]
             for name, values in schema.get("enums", {}).items()}
    return {"messages": messages, "enums": enums}


def schema_changes(old: dict, new: dict) -> list[str]:
    """What moved between two schema.json dicts, one line per difference."""
    a, b = comparable(old), comparable(new)
    out = []
    for msg in sorted(a["messages"].keys() | b["messages"].keys()):
        if msg not in b["messages"]:
            out.append(f"сообщение исчезло: {msg}")
        elif msg not in a["messages"]:
            out.append(f"новое сообщение: {msg}")
        else:
            fa, fb = a["messages"][msg], b["messages"][msg]
            for no in sorted(fa.keys() | fb.keys()):
                if fa.get(no) != fb.get(no):
                    out.append(f"{msg}, поле {no}: {fa.get(no)} -> {fb.get(no)}")
    for enum in sorted(a["enums"].keys() | b["enums"].keys()):
        if a["enums"].get(enum) != b["enums"].get(enum):
            out.append(f"перечисление {enum}: {a['enums'].get(enum)} -> "
                       f"{b['enums'].get(enum)}")
    return out


def check(fetch: Callable[[str], str] = fetch, schema_path: Path = SCHEMA,
          url: str = DEFAULT_URL) -> tuple[bool, list[str]]:
    env = runtime_env(fetch(WIDGET + "runtime-env.js"))
    ok, lines = True, [f"сборка виджета: {env['app_build']}"]
    ours = uuid_of(url)
    if env["uuid"] == ours:
        lines.append("uuid сокета совпадает с DEFAULT_URL")
    else:
        ok = False
        lines.append(f"uuid сокета сменился: у виджета {env['uuid']}, "
                     f"в DEFAULT_URL {ours}")
    name = bundle_name(fetch(WIDGET + "widget.js"))
    new = schema_of(fetch(WIDGET + name))
    if not new["messages"]:
        raise ValueError(f"{name}: no protobuf message literals")
    changes = schema_changes(json.loads(Path(schema_path).read_text(encoding="utf-8")), new)
    if changes:
        ok = False
        lines.append(f"схема в {name} расходится с proto/schema.json "
                     f"({len(changes)}):")
        lines.extend("  " + c for c in changes)
    else:
        lines.append(f"схема в {name} совпадает с proto/schema.json")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    try:
        ok, lines = check()
    except (OSError, ValueError) as exc:     # urllib's errors are OSErrors
        print(f"сверить не удалось: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
