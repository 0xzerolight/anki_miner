"""The Turkish dictionary side: wty-tr-en rows under the Turkish keys, as a card and as the parser reads them.

The rows are wty-tr-en's own (revision 2026.09.19), each cut to its first gloss, imported through the real Yomitan
importer under the profile's keys. Subtitles mostly drop the circumflex that wty keys its headwords with (``pekâlâ``,
``hikâye``), so the key fold drops it on both sides.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages.registry import get_profile
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
