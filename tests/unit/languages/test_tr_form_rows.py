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
from anki_miner.languages._spaced.form_of import FormOfLemmaPass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tr.morphology import tr_row_targets
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


def test_the_parser_repairs_fronts_lemma_rows_first_and_reads_names_as_no_headword(parser):
    injected = parser._token_post_pass  # noqa: SLF001
    assert isinstance(injected, FormOfLemmaPass)
    assert injected._surface_first is False and injected._row_targets is tr_row_targets  # noqa: SLF001


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
