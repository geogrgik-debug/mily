"""Pairing one book's matches with another's by who plays.

Names are the live ones of 22-23.09: BetBoom writes "Тимофеева М." (surname,
initials), 1win "Мария Тимофеева" (given name, surname), both in Russian, and
the two books' Russian spellings of a foreign name can differ by a letter.
"""
import pytest

from tennis.market.join import align_books, name_words, pair_matches, rekey, same_player
from tennis.market.streams import PriceEvent, Stream

T0 = 1_790_175_800_000_000_000
GAME = ("game", 1, 7, "Исход")


def ev(t, match, outcome, prob, prev=None, market=GAME):
    ns = T0 + round(t * 1e9)
    return PriceEvent(ns, ns, match, market, outcome, prob, prev)


def stream(label, provider, events, players):
    return Stream(label, provider, "synthetic", events, [], players)


@pytest.mark.parametrize("name,words", [
    ("Тимофеева М.", {"тимофеева"}),
    ("Мария Тимофеева", {"мария", "тимофеева"}),
    ("Лавиния Пуяк А.", {"лавиния", "пуяк"}),
    ("Чжань Ц.Х.", {"чжань"}),
    ("Руггери Дж.", {"руггери"}),
    ("Семёнова А.", {"семенова"}),
    ("Солорзано A.", {"солорзано"}),                 # a Latin initial, seen live
])
def test_the_words_that_name_a_player(name, words):
    assert name_words(name) == words


@pytest.mark.parametrize("a,b,same", [
    ("Тимофеева М.", "Мария Тимофеева", True),
    ("Риналдо Перссон К.", "Кайса Ринальдо Перссон", True),   # a letter apart
    ("Вискандт М.", "Макс Висканд", True),
    ("Тимофеева М.", "Анна Роджерс", False),
    ("Ли Л.", "Ли На", False),                                   # too short to tell
])
def test_the_same_player_in_two_books(a, b, same):
    assert same_player(a, b) is same


BB_PLAYERS = {5961: ("Тимофеева М.", "Риналдо Перссон К."), 5962: ("Руггери Дж.", "Грабер Дж.")}


def books(onewin_players, onewin_events=None, bb_events=None):
    bb = stream("betboom", "betboom", bb_events or [ev(0, 5961, "П1", 0.5), ev(0, 5962, "П1", 0.5),
                                                    ev(900, 5961, "П1", 0.5)], BB_PLAYERS)
    ow = stream("1win", "1win", onewin_events or [ev(10, 404, "1", 0.5), ev(800, 404, "1", 0.5)],
                onewin_players)
    return bb, ow


def test_a_match_pairs_by_its_players_and_the_sides_follow_them():
    bb, ow = books({404: ("Мария Тимофеева", "Кайса Ринальдо Перссон")})
    assert pair_matches(bb, ow)[0] == {404: (5961, {"1": "П1", "2": "П2"})}


def test_home_and_away_swapped_between_the_books():
    bb, ow = books({404: ("Кайса Ринальдо Перссон", "Мария Тимофеева")})
    assert pair_matches(bb, ow)[0] == {404: (5961, {"1": "П2", "2": "П1"})}


def test_the_same_players_on_another_day_are_another_match():
    bb, ow = books({404: ("Мария Тимофеева", "Кайса Ринальдо Перссон")},
                   onewin_events=[ev(90_000, 404, "1", 0.5)])
    paired, counts = pair_matches(bb, ow)
    assert paired == {} and counts["unpaired"] == 1


def test_two_matches_claiming_one_are_both_left_out():
    """One to one both ways: were two of 1win's matches to find the same
    BetBoom match, neither would be trusted."""
    bb, ow = books({404: ("Мария Тимофеева", "Кайса Ринальдо Перссон"),
                    405: ("Мария Тимофеева", "Кайса Ринальдо Перссон")},
                   onewin_events=[ev(10, 404, "1", 0.5), ev(20, 405, "1", 0.5)])

    paired, counts = pair_matches(bb, ow)

    assert paired == {} and counts["ambiguous"] == 2


def test_one_player_in_common_is_not_a_match():
    bb, ow = books({404: ("Мария Тимофеева", "Анна Роджерс")})
    assert pair_matches(bb, ow)[0] == {}


def test_rekeyed_events_take_the_other_books_match_and_side():
    bb, ow = books({404: ("Кайса Ринальдо Перссон", "Мария Тимофеева")},
                   onewin_events=[ev(10, 404, "1", 0.30, 0.25), ev(10, 404, "2", 0.70, 0.75),
                                  ev(20, 999, "1", 0.5, 0.4)])

    out, note = rekey(ow, bb)

    assert [(e.match, e.outcome) for e in out.events] == [(5961, "П2"), (5961, "П1")]
    assert note == "1win: 1 of 2 matches paired with betboom's by the players' names"


def test_books_are_aligned_onto_the_first_and_one_book_is_left_alone():
    bb, ow = books({404: ("Мария Тимофеева", "Кайса Ринальдо Перссон")})
    same_book = stream("B", "betboom", [ev(0, 5961, "П1", 0.5)], BB_PLAYERS)

    out, notes = align_books([bb, ow, same_book])

    assert out[0] is bb and out[2] is same_book
    assert {e.match for e in out[1].events} == {5961}
    assert len(notes) == 1
