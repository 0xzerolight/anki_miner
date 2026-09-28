"""The Turkish dictionary side: wty-tr-en rows under the Turkish keys, as a card and as the parser reads them.

The rows are wty-tr-en's own (revision 2026.09.19), each cut to its first gloss, imported through the real Yomitan
importer under the profile's keys. Subtitles mostly drop the circumflex that wty keys its headwords with (``pekâlâ``,
``hikâye``), so the key fold drops it on both sides.

The parser half reads those rows through ``IndexedDictProvider.term_rows``, the R36 ``form_lookup`` that
``service_factory`` wires, over the real zeyrek tagger. The parser is module-scoped: the autouse conftest fixture
clears the tagger cache around every test, and a zeyrek build costs seconds.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.tr.morphology import HeadwordPosPass, tr_row_targets
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "tr" / "wty_rows.json").read_text(encoding="utf-8"))


def _lemma(term: str, tags: str, gloss: str) -> list[object]:
    return [term, "", tags, "", 0, [gloss], 0, ""]


def _form(term: str, *targets: str) -> list[object]:
    return [term, "", "non-lemma", "", 0, [[target, ["inflection"]] for target in targets], 0, ""]


#: In wty's import order: a plain spelling's rows come before its circumflex homograph's (kar 1818, kâr 2721).
ROWS = [
    _lemma("kar", "n", "snow"),
    _lemma("kâr", "n", "profit"),
    _form("kar", "karmak"),
    _lemma("pekâlâ", "adv", "all right"),
    _lemma("pekâlâ", "intj", "alright, okay"),
    _lemma("hikâye", "n", "story, tale"),
    _form("hikâye", "hikaye"),
    _form("hikaye", "hikâye"),
    _form("bitti", "bitmek", "bitmek"),
    _lemma("bitmek", "v vi", "to end, to finish"),
    _form("günü", "gün"),
    _lemma("gün", "n", "day"),
    _form("adamı", "ada"),
    _form("adamı", "adam"),
    _lemma("ada", "n", "island"),
    _lemma("adam", "n", "man"),
    _lemma("Evin", "name", "a female given name"),
    _form("evin", "ev", "ev"),
    _lemma("ev", "n", "house, home"),
    _lemma("çocuk", "n", "child"),
    _lemma("dakika", "n", "minute (unit of time)"),
    _lemma("güzel", "adj", "beautiful"),
    _lemma("güzel", "n", "beauty"),
    _lemma("Güzel", "name", "a female given name"),
]


@pytest.fixture(scope="module")
def provider(tmp_path_factory):
    folder = tmp_path_factory.mktemp("wty-tr-en")
    archive = folder / "wty-tr-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(ROWS, ensure_ascii=False))
    result = import_yomitan_zip(archive, folder / "dicts", dict_id="wty-tr-en", language="tr")
    assert result.entry_count == len(ROWS)
    provider = IndexedDictProvider(
        "wty-tr-en", folder / "dicts" / "wty-tr-en" / "index.sqlite", keys=get_profile("tr").dict_keys
    )
    assert provider.load()
    return provider


@pytest.mark.parametrize(("written", "gloss"), [("pekala", "all right"), ("Pekala", "all right"), ("hikaye", "story")])
def test_a_spelling_without_the_circumflex_reaches_the_headword_s_senses(provider, written, gloss):
    """``pekala`` had no row at all (no card), ``hikaye`` only a pointer to ``hikâye``."""
    assert gloss in (provider.lookup(written) or "")


def test_the_plain_spelling_of_a_homograph_keeps_its_sense_first(provider):
    card = provider.lookup("kar") or ""
    assert card.index("snow") < card.index("profit")


# --------------------------------------------------------------------------
# The parser's post-pass
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parser(provider):
    """The real Turkish parser with the index as its form lookup, as ``service_factory`` builds it."""
    return get_profile("tr").create_parser(switch_language(AnkiMinerConfig(), "tr"), form_lookup=provider.term_rows)


def _mined(parser, line: str) -> dict[str, tuple[str, str | None]]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="t")], False)
    return {word.surface: (word.mined_form, word.pos) for word in words}


def test_the_parser_labels_then_repairs_fronts_lemma_rows_first_and_reads_names_as_no_headword(parser):
    injected = parser._token_post_pass  # noqa: SLF001
    assert isinstance(injected, OrderedPasses)
    label, repair = injected._passes  # noqa: SLF001
    assert isinstance(label, HeadwordPosPass) and isinstance(repair, FormOfLemmaPass)
    assert repair._surface_first is False and repair._row_targets is tr_row_targets  # noqa: SLF001


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Her şey bitti.", "bitti", ("bitmek", "VERB")),  # zeyrek's noun bitti is only wty's form of bitmek
        ("Doğum günü kutlu olsun!", "günü", ("gün", "NOUN")),
        ("Hemen evine git.", "evine", ("ev", "NOUN")),  # evin is a given name and a form of ev
    ],
)
def test_a_front_the_dictionary_files_only_as_a_form_takes_its_headword(parser, line, surface, front):
    assert _mined(parser, line)[surface] == front


def test_a_form_of_two_headwords_keeps_the_tagger_s_front(parser):
    """wty files ``adamı`` under ``ada`` and under ``adam``: the pass cannot tell which."""
    assert _mined(parser, "Şu adamı tanıyor musun?")["adamı"] == ("adamı", "NOUN")


def test_a_spelling_without_the_circumflex_is_a_headword_not_a_form(parser):
    """``hikaye`` reaches ``hikâye``'s rows through the key fold; the front keeps the subtitle's spelling."""
    assert _mined(parser, "Bana bir hikaye anlat.")["hikaye"] == ("hikaye", "NOUN")


# --------------------------------------------------------------------------
# The part of speech the dictionary gives
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Çocuk uyuyor.", "Çocuk", ("çocuk", "NOUN")),  # zeyrek ranks the zero-morpheme Adj reading first
        ("Bir dakika bekle.", "dakika", ("dakika", "NOUN")),  # ... and Adv here
        ("Pekala, gidelim.", "Pekala", ("pekala", "ADV")),  # pekâlâ's rows, through the circumflex fold
        ("Çok güzel.", "güzel", ("güzel", "ADJ")),  # an adj row supports the pick
    ],
)
def test_the_pos_is_the_one_the_lemma_s_headword_rows_give(parser, line, surface, front):
    assert _mined(parser, line)[surface] == front


class Rows:
    """``FormLookup`` over a few lemmas' ``(content, tags)`` rows; records every batch it is asked."""

    def __init__(self, **rows: str) -> None:
        self._rows = {
            lemma: [("<li>a sense</li>", tags) for tags in all_tags.split(",")] for lemma, all_tags in rows.items()
        }
        self.calls: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.calls.append(list(terms))
        return {term: self._rows[term] for term in terms if term in self._rows}


def _labelled(forms: Rows | None, lemma: str, pos1: str, *lemma_pos: str, label: HeadwordPosPass | None = None) -> str:
    token = LanguageToken(lemma, pos1, lemma=lemma)
    token.feature.lemma_pos = lemma_pos
    (token,) = (label or HeadwordPosPass())([token], None, forms)
    return token.feature.pos1


def test_a_pick_the_rows_do_not_support_takes_a_reading_they_do():
    assert _labelled(Rows(çocuk="n"), "çocuk", "ADJ", "ADJ", "NOUN") == "NOUN"
    assert _labelled(Rows(dakika="n"), "dakika", "ADV", "ADV", "NOUN") == "NOUN"
    assert _labelled(Rows(hayır="intj,n,name"), "hayır", "ADJ", "ADJ", "ADV", "NOUN", "INTJ") == "NOUN"


def test_zeyrek_s_order_picks_between_two_readings_the_rows_support():
    """wty-tr-en ``artık`` opens on the noun "remnant"; subtitles mean the adverb "anymore", which zeyrek ranks first."""
    assert _labelled(Rows(artık="n,adv"), "artık", "ADJ", "ADJ", "ADV", "NOUN") == "ADV"


def test_a_pick_the_rows_support_keeps_its_label():
    assert _labelled(Rows(güzel="adj,n,name"), "güzel", "ADJ", "ADJ", "ADV", "NOUN") == "ADJ"


def test_a_noun_pick_keeps_its_label():
    """An inflected noun wins zeyrek's count through its ending: ``zorunda`` is ``zor`` + possessive + locative."""
    assert _labelled(Rows(zor="adj"), "zor", "NOUN", "NOUN", "ADJ") == "NOUN"


def test_form_and_name_rows_support_no_label_and_only_mined_classes_trade():
    assert _labelled(Rows(sevgili="name,non-lemma"), "sevgili", "ADJ", "ADJ", "NOUN") == "ADJ"
    assert _labelled(Rows(aman="intj"), "aman", "ADJ", "ADJ", "INTJ") == "ADJ"  # INTJ is never mined


def test_nothing_is_read_without_a_dictionary_or_a_second_reading():
    assert _labelled(None, "çocuk", "ADJ", "ADJ", "NOUN") == "ADJ"
    forms = Rows(kitap="n")
    assert _labelled(forms, "kitap", "NOUN", "NOUN") == "NOUN" and forms.calls == []


def test_a_lemma_is_read_once_per_pass():
    forms, label = Rows(çocuk="n"), HeadwordPosPass()
    assert [_labelled(forms, "çocuk", "ADJ", "ADJ", "NOUN", label=label) for _ in range(2)] == ["NOUN", "NOUN"]
    assert forms.calls == [["çocuk"]]
