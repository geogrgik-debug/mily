"""Read a recorded raw log back and say what the server actually sent.

This exists because the recorder is nearly silent by design: it prints on a
dropped connection and on a subscription, and otherwise lets the raw log be the
record. When a session ends with frames on disk but nothing subscribed, the
answer is in those frames, and guessing at field names is exactly the wrong way
to find it. So this walks the log and reports the structure by protobuf
reflection -- `ListFields` on what arrived -- rather than by reading the fields
this project expects to be there.

    python -m tennis.ingest.betboom.inspect_log data/raw

Options:
    --depth N     how deep to walk nested messages (default 6)
    --max-items N how many repeated entries to expand per level (default 3)
    --type NAME   dump only frames whose response oneof is NAME
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from tennis.ingest.betboom.client import load_pb
from tennis.ingest.rawlog import find_logs, read_raw

SCALARS = (bool, int, float, str, bytes)

# Set once in main(); _unpack needs the generated module and is called from
# deep inside the walk, where threading it through every frame would be noise.
_PB: list = [None]


def _is_repeated(descriptor, value) -> bool:
    """True for a repeated or map field.

    protobuf's upb implementation dropped FieldDescriptor.label, so the
    attribute the pure-Python runtime exposes cannot be relied on. `is_repeated`
    exists on recent versions; older ones fall back to shape, since only a
    repeated container has a length and no ListFields.
    """
    flag = getattr(descriptor, "is_repeated", None)
    if flag is not None:
        return bool(flag)
    return (not isinstance(value, SCALARS)
            and hasattr(value, "__len__")
            and not hasattr(value, "ListFields"))


def _is_map(descriptor) -> bool:
    message_type = getattr(descriptor, "message_type", None)
    if message_type is None:
        return False
    try:
        return bool(message_type.GetOptions().map_entry)
    except Exception:
        return False


def _unpack(value: bytes) -> list[str]:
    """Best-effort decode of a packed detail blob.

    Errors arrive with their specifics in a `google.protobuf.Any`, and the one
    that matters names the field the server refused. Trying the known detail
    type is cheap; a blob of any other shape just fails to parse and is left as
    bytes.
    """
    pb = _PB[0]
    if pb is None:
        return []
    detail = getattr(pb, "common_BadRequestErrorDetails", None)
    if detail is None:
        return []
    try:
        parsed = detail()
        parsed.ParseFromString(value)
    except Exception:
        return []
    return [f"violation {v.reason!r}: {v.message!r}" for v in parsed.violations] \
        or ["(decoded as BadRequestErrorDetails, no violations)"]


def _describe(msg, depth: int, max_items: int, indent: int = 0) -> list[str]:
    """Lines describing which fields are actually populated on `msg`."""
    out: list[str] = []
    pad = "  " * indent
    try:
        fields = msg.ListFields()
    except AttributeError:
        return [f"{pad}{msg!r}"]
    if not fields:
        out.append(f"{pad}(no fields set)")
    for descriptor, value in fields:
        name = descriptor.name
        if _is_map(descriptor):
            keys = list(value)[:max_items]
            out.append(f"{pad}{name}{{}} : {len(value)} key(s), e.g. {keys}")
            continue
        if _is_repeated(descriptor, value):
            items = list(value)
            out.append(f"{pad}{name}[] : {len(items)} item(s)")
            if depth <= 0:
                continue
            for item in items[:max_items]:
                if isinstance(item, SCALARS):
                    out.append(f"{pad}  - {item!r}")
                else:
                    out.append(f"{pad}  -")
                    out.extend(_describe(item, depth - 1, max_items, indent + 2))
            if len(items) > max_items:
                out.append(f"{pad}  ... {len(items) - max_items} more")
        elif isinstance(value, bytes):
            # Never silently truncate: the first version showed value[:40], and
            # the one blob that mattered -- a packed error detail naming the
            # field the server refused -- was longer than that, so it could not
            # be decoded from the printed output. Length first, then the bytes.
            out.append(f"{pad}{name} = <{len(value)} bytes> {value!r}")
            for line in _unpack(value):
                out.append(f"{pad}  ^ {line}")
        elif isinstance(value, SCALARS):
            out.append(f"{pad}{name} = {value!r}")
        else:
            out.append(f"{pad}{name}:")
            if depth > 0:
                out.extend(_describe(value, depth - 1, max_items, indent + 1))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default="data/raw")
    ap.add_argument("--depth", type=int, default=6,
                    help="how deep to walk nested messages; the live tree is "
                         "about five levels, so the default is generous")
    ap.add_argument("--max-items", type=int, default=3)
    ap.add_argument("--type", default=None,
                    help="only dump frames whose response oneof matches this")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f"nothing at {root}. The recorder writes under --out (default "
              f"data/raw); run it from the repository root, or pass the path.",
              file=sys.stderr)
        return 1
    files = find_logs(root)
    if not files:
        print(f"no .jsonl or .jsonl.gz under {root}", file=sys.stderr)
        return 1
    print(f"log files: {[str(f) for f in files]}")

    pb = load_pb()
    _PB[0] = pb

    frames = Counter()          # (dir, channel)
    tags = Counter()            # tx tags, so the request side is visible too
    kinds = Counter()           # rx response oneof
    unparsed = 0
    meta_lines: list[str] = []
    first_of_kind: dict[str, object] = {}

    for path in files:
        for row in read_raw(path):
            direction = row.get("dir", "?")
            channel = row.get("channel", "")
            frames[(direction, channel)] += 1
            if direction == "meta" or channel == "_conn":
                payload = row.get("payload")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", "replace")
                meta_lines.append(str(payload)[:300])
                continue
            if direction == "tx":
                tag = (row.get("meta") or {}).get("tag", "?")
                tags[tag] += 1
                continue
            payload = row.get("payload")
            if not isinstance(payload, bytes):
                continue
            msg = pb.MainResponse()
            try:
                msg.ParseFromString(payload)
            except Exception:
                unparsed += 1
                continue
            which = msg.WhichOneof("type") or "(no oneof set)"
            kinds[which] += 1
            first_of_kind.setdefault(which, msg)

    print("\n=== frames by direction/channel ===")
    for (direction, channel), n in frames.most_common():
        print(f"  {n:6d}  {direction:5s} {channel}")

    print("\n=== requests we sent ===")
    for tag, n in tags.most_common():
        print(f"  {n:6d}  {tag}")

    print("\n=== responses by type ===")
    if not kinds:
        print("  none parsed as MainResponse")
    for which, n in kinds.most_common():
        print(f"  {n:6d}  {which}")
    if unparsed:
        print(f"  {unparsed:6d}  FAILED to parse as MainResponse")

    if meta_lines:
        print("\n=== meta / connection notes ===")
        for line in meta_lines[:20]:
            print(f"  {line}")

    print("\n=== structure of the first frame of each type ===")
    for which, msg in first_of_kind.items():
        if args.type and which != args.type:
            continue
        print(f"\n--- {which} ---")
        for line in _describe(msg, args.depth, args.max_items):
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
