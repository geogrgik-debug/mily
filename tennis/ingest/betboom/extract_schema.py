#!/usr/bin/env python3
"""Recover the BetBoom live-feed protobuf schema from the shipped JS bundle.

BetBoom does not serve its betting line over HTTP. The site's sportsbook is a
white-label of the sporthub.bet platform, and every price arrives over

    wss://{partner}-ws2.sporthub.bet:443/api/tree_ws/v1

as protobuf frames (`FEED_WS_PROTOCOL: "protobuf"` in the widget's
runtime-env.js). There is no REST fallback: with the socket blocked the page
renders an empty shell.

The schema is not published, but the client is built with protobuf-es v1,
which compiles every message into a literal field list in the JS bundle. That
makes the wire format fully recoverable without ever touching the socket:

    <ident> = new class extends <base> {
        constructor() { super("bb.sport_ws.v1.models.ModelsStake", [
            {no:1, name:"stake_id", kind:"scalar", T:9}, ...
        ]) }
    }

This script parses those literals and emits .proto files. Re-run it whenever
`APP_BUILD` in runtime-env.js changes; the field numbers are the contract, and
a silent renumbering would corrupt a recording without raising an error.

Usage:
    python -m tennis.ingest.betboom.extract_schema <bundle.js> [-o outdir]

Finding the bundle (it is content-hashed, so the name changes on every
deploy): load https://betboom.ru/sport/live in a browser, take the
`vendor-core-*.js` request from
https://sportbook.sporthub.bet/widgets/sportbook/v1/modern/. Or no browser:
widget.js in that directory names it, which is how check_build finds it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# @bufbuild/protobuf ScalarType -> proto3 type name. These are the protobuf
# field-type numbers, so the mapping is the spec's, not the library's.
SCALAR = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 12: "bytes",
    13: "uint32", 15: "sfixed32", 16: "sfixed64", 17: "sint32", 18: "sint64",
}

_DECL = re.compile(r'([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new class extends\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\{\s*constructor\(\)\s*\{\s*super\("([A-Za-z0-9_.]+)",')
_FIELD = re.compile(
    r'\{\s*no:\s*(?P<no>\d+)\s*,\s*name:\s*"(?P<name>[^"]+)"'
    r'(?:\s*,\s*kind:\s*"(?P<kind>[a-z]+)")?'
    r'(?P<rest>[^{}]*(?:\{[^{}]*\}[^{}]*)*)'
)
_TREF = re.compile(r'T:\s*\(\)\s*=>\s*([A-Za-z_$][A-Za-z0-9_$]*)')
_TNUM = re.compile(r'T:\s*(\d+)')
_ENUM_INLINE = re.compile(
    r'T:\s*\(\)\s*=>\s*\[\s*"([A-Za-z0-9_.]+)"\s*,\s*([A-Za-z_$][A-Za-z0-9_$]*)'
    r'(?:\s*,\s*"([A-Z0-9_]*)")?'
)
_ENUM_DECL = re.compile(
    r'([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\(?function\((\w)\)\{return((?:\s*\2\[\2\.[A-Z0-9_]+=\d+\]="[A-Z0-9_]+",?)+)'
)
_ENUM_PAIR = re.compile(r'\[\w\.([A-Z0-9_]+)=(\d+)\]')


def _match_bracket(src: str, start: int, op: str = "[", cl: str = "]") -> str | None:
    """Return the bracketed slice beginning at `start`, respecting strings."""
    if src[start] != op:
        return None
    depth = 0
    k = start
    while k < len(src):
        ch = src[k]
        if ch in "\"'":
            quote = ch
            k += 1
            while k < len(src) and src[k] != quote:
                k += 2 if src[k] == "\\" else 1
        elif ch == op:
            depth += 1
        elif ch == cl:
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
        k += 1
    return None


def parse_enums(src: str) -> dict[str, list[tuple[str, int]]]:
    """Minified ident -> [(value name, number)].

    protobuf-es v1 compiles an enum to the TypeScript reverse-mapping idiom
    `X = (function(e){ return e[e.HOME=1]="HOME", ... })({})`, and the field
    that uses it carries the proto type name alongside the ident.
    """
    out: dict[str, list[tuple[str, int]]] = {}
    for m in _ENUM_DECL.finditer(src):
        pairs = [(n, int(v)) for n, v in _ENUM_PAIR.findall(m.group(3))]
        if pairs:
            out[m.group(1)] = sorted(pairs, key=lambda kv: kv[1])
    return out


def parse_bundle(src: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Return (typename -> fields, minified ident -> typename)."""
    ident_to_type: dict[str, str] = {}
    spans: list[tuple[str, int]] = []
    for m in _DECL.finditer(src):
        ident, typename = m.group(1), m.group(2)
        ident_to_type[ident] = typename
        spans.append((typename, m.end() - 1))

    messages: dict[str, list[dict]] = {}
    for typename, comma_at in spans:
        bracket = src.find("[", comma_at)
        if bracket < 0 or bracket > comma_at + 4:
            messages[typename] = []          # message with no fields
            continue
        arr = _match_bracket(src, bracket)
        if arr is None:
            print(f"warn: unbalanced field list for {typename}", file=sys.stderr)
            continue
        messages[typename] = _parse_fields(arr)
    return messages, ident_to_type


def schema_of(src: str) -> dict:
    """The schema as schema.json keeps it: messages, the minified idents that
    name message types, and each enum type's values. check_build compares a
    live bundle by this same dict."""
    messages, ident_to_type = parse_bundle(src)
    enums = parse_enums(src)
    enum_ident = {f["type_ref"]: f["enum_ident"]
                  for fields in messages.values() for f in fields
                  if f.get("kind") == "enum" and f.get("enum_ident")}
    return {"messages": messages, "idents": ident_to_type,
            "enums": {t: enums.get(i, []) for t, i in enum_ident.items()}}


def _parse_fields(arr: str) -> list[dict]:
    out = []
    for m in _FIELD.finditer(arr):
        rest = m.group("rest") or ""
        field = {
            "no": int(m.group("no")),
            "name": m.group("name"),
            "kind": m.group("kind") or "scalar",
            "repeated": bool(re.search(r"repeat:\s*\d", rest)),
            "optional": "opt:!0" in rest.replace(" ", ""),
        }
        oneof = re.search(r'oneof:\s*"([^"]+)"', rest)
        if oneof:
            field["oneof"] = oneof.group(1)
        enum_inline = _ENUM_INLINE.search(rest)
        tref = _TREF.search(rest)
        tnum = _TNUM.search(rest)
        if enum_inline:
            field["type_ref"] = enum_inline.group(1)
            field["enum_ident"] = enum_inline.group(2)
            field["enum_prefix"] = enum_inline.group(3) or ""
            field["kind"] = "enum"
        elif tref:
            field["type_ref"] = tref.group(1)
        elif tnum:
            field["scalar"] = int(tnum.group(1))
        out.append(field)
    return out


def _proto_type(field: dict, ident_to_type: dict[str, str]) -> str:
    if field["kind"] == "scalar" or "scalar" in field:
        return SCALAR.get(field.get("scalar", 9), "bytes")
    ref = field.get("type_ref", "")
    return ident_to_type.get(ref, ref or "bytes")


def render_proto(messages: dict[str, list[dict]], ident_to_type: dict[str, str],
                 package: str, enums: dict[str, list[tuple[str, int]]] | None = None,
                 enum_types: dict[str, str] | None = None,
                 enum_prefix: dict[str, str] | None = None) -> str:
    """Emit one .proto file for a single package prefix.

    Nested message names are flattened with underscores: the wire format does
    not care about nesting, and a flat file is far easier to diff between
    deploys, which is the point of keeping this checked in.
    """
    def flat(name: str) -> str:
        # Well-known types keep their real names and are imported, not inlined.
        if name.startswith("google.protobuf."):
            return "." + name
        return name[len(package) + 1:].replace(".", "_") if name.startswith(package + ".") else name.replace(".", "_")

    needs_any = any(
        f.get("type_ref", "").startswith("google.protobuf.Any")
        or ident_to_type.get(f.get("type_ref", ""), "").startswith("google.protobuf.Any")
        for name, fields in messages.items() if name.startswith(package + ".")
        for f in fields
    )

    lines = [
        'syntax = "proto3";', "",
        f"package {package};", "",
        *(['import "google/protobuf/any.proto";', ""] if needs_any else []),
        "// Recovered from the shipped BetBoom/sporthub JS bundle by",
        "// tennis/ingest/betboom/extract_schema.py. Not an official schema:",
        "// field numbers are read off the client and must be re-checked",
        "// whenever the widget's APP_BUILD changes.", "",
    ]
    for enum_name in sorted(enum_types or {}):
        if not enum_name.startswith(package + "."):
            continue
        values = (enums or {}).get((enum_types or {})[enum_name], [])
        if not values:
            continue
        # protobuf-es strips a common prefix from every value; restore it,
        # both because it is the real name on the wire's schema and because
        # proto enum values are siblings of their type, so bare UNSPECIFIED
        # would collide across the 14 enums in this package.
        prefix = (enum_prefix or {}).get(enum_name, "")
        lines.append(f"enum {flat(enum_name)} {{")
        for vname, vnum in values:
            lines.append(f"  {prefix}{vname} = {vnum};")
        lines.append("}")
        lines.append("")

    for typename in sorted(messages):
        if not typename.startswith(package + "."):
            continue
        fields = messages[typename]
        lines.append(f"message {flat(typename)} {{")
        groups: dict[str, list[dict]] = defaultdict(list)
        plain = []
        for f in fields:
            (groups[f["oneof"]] if "oneof" in f else plain).append(f)
        for f in plain:
            t = _proto_type(f, ident_to_type)
            t = flat(t) if t in messages or t in (enum_types or {}) else t
            label = "repeated " if f["repeated"] else ("optional " if f["optional"] else "")
            lines.append(f"  {label}{t} {f['name']} = {f['no']};")
        for gname, gfields in groups.items():
            lines.append(f"  oneof {gname} {{")
            for f in gfields:
                t = _proto_type(f, ident_to_type)
                t = flat(t) if t in messages or t in (enum_types or {}) else t
                lines.append(f"    {t} {f['name']} = {f['no']};")
            lines.append("  }")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", type=Path, help="vendor-core-*.js from the sportbook widget")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("tennis/ingest/betboom/proto"))
    ap.add_argument("--packages", nargs="*", default=["bb.sport_ws.v1"],
                    help="package prefixes to emit; 'all' for every one found")
    args = ap.parse_args(argv)

    src = args.bundle.read_text(encoding="utf-8", errors="replace")
    schema = schema_of(src)
    messages, ident_to_type, enums = schema["messages"], schema["idents"], schema["enums"]
    # schema_of already keys enum values by type name, so each type is its own key.
    enum_types = {name: name for name in enums}
    enum_prefix = {f["type_ref"]: f.get("enum_prefix", "")
                   for fields in messages.values() for f in fields
                   if f.get("kind") == "enum" and f.get("enum_ident")}
    if not messages:
        print("no protobuf message literals found — wrong bundle?", file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")

    prefixes = sorted({".".join(n.split(".")[:3]) for n in messages}) \
        if args.packages == ["all"] else args.packages
    for pkg in prefixes:
        text = render_proto(messages, ident_to_type, pkg, enums, enum_types, enum_prefix)
        path = args.outdir / (pkg.replace(".", "_") + ".proto")
        path.write_text(text, encoding="utf-8")
        n = sum(1 for k in messages if k.startswith(pkg + "."))
        print(f"{path}: {n} messages")

    total_fields = sum(len(v) for v in messages.values())
    print(f"{len(messages)} messages, {total_fields} fields, "
          f"{len(enum_types)} enums, {len(ident_to_type)} idents resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
