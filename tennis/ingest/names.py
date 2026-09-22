"""Cross-provider identity for players and matches.

Every provider invents its own match ids, so the only way to line two feeds
up — which is the whole point of the latency bake-off — is to rebuild a key
from what they agree on: who is playing and when.

The name normalisation is the one already used in `research/`: strip to
ASCII, reduce to initial plus surname. That form survives the two spellings
the feeds actually mix ("Novak Djokovic" and "N. Djokovic"), which is
exactly the defect that sent 762 men's matches into the women's sample in
experiment B2.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

# Surname particles that belong to the surname, not to the given names.
_PARTICLES = {
    "de", "del", "della", "der", "di", "du", "da", "dos", "das",
    "van", "von", "ten", "ter", "la", "le", "al", "bin", "ibn", "mc", "mac",
}

_INITIAL = re.compile(r"^[a-z]\.?$")
_NOT_NAME = re.compile(r"[^a-z\s'\-\.]")


def _ascii_fold(text: str) -> str:
    """Drop accents: Cilic and Čilić must land on the same key."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_player(name: str) -> str:
    """Reduce a player name to `initial.surname`, lowercase ASCII.

    >>> normalize_player("Novak Djokovic")
    'n.djokovic'
    >>> normalize_player("N. Djokovic")
    'n.djokovic'
    >>> normalize_player("Djokovic N.")
    'n.djokovic'
    >>> normalize_player("Felix Auger-Aliassime")
    'f.auger-aliassime'
    >>> normalize_player("Juan Martin del Potro")
    'j.del-potro'

    Returns "" for input that carries no usable name, so callers can treat
    an unidentifiable match as unidentifiable rather than silently keying
    several different matches to the same empty string pair.
    """
    if not name:
        return ""
    text = _ascii_fold(name).lower().replace("_", " ")
    text = _NOT_NAME.sub(" ", text)
    tokens = [t for t in text.split() if t and t != "."]
    if not tokens:
        return ""

    # Feeds write either "N. Djokovic" or "Djokovic N."; the single-letter
    # token is the initial wherever it sits.
    initials = [i for i, t in enumerate(tokens) if _INITIAL.match(t)]
    if initials:
        idx = initials[0]
        initial = tokens[idx][0]
        rest = tokens[:idx] + tokens[idx + 1 :]
        rest = [t for t in rest if not _INITIAL.match(t)]
    else:
        # Full names come in western order: given name(s) then surname.
        initial = tokens[0][0]
        rest = tokens[1:]
        if not rest:  # a single token is a surname, not a given name
            initial = ""
            rest = tokens

    # Keep particles attached to the surname, drop any leading given names.
    surname_start = 0
    for i, tok in enumerate(rest):
        if tok in _PARTICLES:
            surname_start = i
            break
    else:
        surname_start = max(0, len(rest) - 1)
        # A hyphenated or two-part surname arrives as one token already,
        # unless the feed split it; keep the trailing two when the first of
        # them is not a plausible given name (it has no vowel-only shape).
    surname = "-".join(t.strip(".-") for t in rest[surname_start:] if t.strip(".-"))
    surname = re.sub(r"-{2,}", "-", surname).strip("-")
    if not surname:
        return ""
    return f"{initial}.{surname}" if initial else surname


def match_key(player_a: str, player_b: str, when: date | str) -> str:
    """Provider-independent key for one match.

    The player pair is sorted, so it does not matter which side a provider
    calls "home". The date is the match's local calendar date as the
    provider reports it; use `candidate_keys` when joining across providers,
    because a match that runs past midnight is dated differently by
    different feeds.
    """
    a = normalize_player(player_a)
    b = normalize_player(player_b)
    if not a or not b:
        raise ValueError(f"unidentifiable player pair: {player_a!r} vs {player_b!r}")
    lo, hi = sorted((a, b))
    day = when.isoformat() if isinstance(when, date) else str(when)[:10]
    return f"{day}|{lo}|{hi}"


def candidate_keys(player_a: str, player_b: str, when: date | str) -> list[str]:
    """`match_key` plus the neighbouring days, for cross-provider joining.

    A night session that finishes after midnight UTC is filed by one feed
    under the day it started and by another under the day it ended. Matching
    on the exact date alone silently loses those matches.
    """
    day = when if isinstance(when, date) else date.fromisoformat(str(when)[:10])
    return [match_key(player_a, player_b, day + timedelta(days=d)) for d in (0, -1, 1)]


def key_players(key: str) -> tuple[str, str]:
    """The normalised player pair carried by a match key."""
    parts = key.split("|")
    if len(parts) != 3:
        raise ValueError(f"not a match key: {key!r}")
    return parts[1], parts[2]
