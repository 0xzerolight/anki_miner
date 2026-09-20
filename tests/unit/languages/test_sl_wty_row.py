"""Real wty-sl-en rows survive import + render under the Slovenian keys; the hook prints gender and aspect."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.sl.morphology import sl_tone_fold
from anki_miner.services.dictionary.importers.yomitan_importer import declares_other_language, import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sl"
ROW = json.loads((FIXTURES / "wty_row.json").read_text(encoding="utf-8"))
TAGS = json.loads((FIXTURES / "tag_bank_sample.json").read_text(encoding="utf-8"))
HEAD_RE = re.compile(r'data-sc-content="Grammar-content">([^<]*)<')


def _provider(tmp_path, fixture, name):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(fixture["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(fixture["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(fixture["term_rows"]))
    import_yomitan_zip(archive, tmp_path / name, dict_id="wty-sl-en", language="sl")
    loaded = IndexedDictProvider(
        "wty-sl-en", tmp_path / name / "wty-sl-en" / "index.sqlite", keys=get_profile("sl").dict_keys
    )
    assert loaded.load()
    return loaded


@pytest.fixture
def provider(tmp_path):
    return _provider(tmp_path, ROW, "dicts")


@pytest.fixture
def verbs(tmp_path):
    return _provider(tmp_path, TAGS, "verbs")


def test_the_fixture_carries_its_licence_and_declares_slovene():
    assert "CC BY-SA 4.0" in ROW["license"] and ROW["index"]["sourceLanguage"] == "sl"


def test_an_sl_profile_accepts_only_the_slovene_section():
    """Unlike hr there is no wiktionary_code redirection: only sl is sl's own tree."""
    assert get_profile("sl").wiktionary_code == ""
    assert declares_other_language("sl", "sl", "") is False
    assert declares_other_language("sh", "sl", "") is True
    assert declares_other_language("", "sl", "") is False


@pytest.mark.parametrize("term", sorted(ROW["rendered_html"]))
def test_every_row_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == ROW["rendered_html"][term]


def _render(term: str, html: str | None, morph: str = "", pos: str = "NOUN") -> dict[str, str]:
    hook = get_profile("sl").render_hooks[1]
    word = SimpleNamespace(pos=pos, mined_form=term, surface=term, morph=morph, definition_html=html or "")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_gender_comes_from_the_chips_when_there_is_no_head_line(provider):
    """knjiga carries NO Grammar block - 93.2 % of noun rows do not - so the chips answer."""
    html = provider.lookup("knjiga")
    assert html is not None and "Grammar-content" not in html
    assert _render("knjiga", html) == {"noun_gender": "feminine"}
    assert _render("miza", provider.lookup("miza")) == {"noun_gender": "feminine"}
    assert _render("mesto", provider.lookup("mesto")) == {"noun_gender": "neuter"}
    assert _render("cvet", provider.lookup("cvet")) == {"noun_gender": "masculine"}


def test_the_morph_wins_when_the_dictionary_says_nothing(provider):
    """morje has one row with no gender chip and one adverb row; only word.morph knows it is neuter."""
    html = provider.lookup("morje")
    assert html is not None and "gender-" not in html
    assert _render("morje", html) == {}
    assert _render("morje", html, morph="Case=Nom|Gender=Neut|Number=Sing") == {"noun_gender": "neuter"}


def test_an_animacy_qualifier_and_an_accent_headword_do_not_hide_the_gender(provider):
    """D2: the head line is the schwa spelling plus ``m anim``; the shared pre-fold strips the mark,
    the seam skips the qualifier, and sl declares no animacy labels."""
    html = provider.lookup("pes")
    assert html is not None
    (head,) = HEAD_RE.findall(html)
    assert head.split()[1:3] == ["m", "anim"]
    assert _render("pes", html) == {"noun_gender": "masculine"}


def test_the_aspect_pair_comes_from_the_head_line_with_the_tone_fold(provider):
    """The partner is written in accent notation the card must not print."""
    html = provider.lookup("brati")
    assert html is not None
    assert _render("brati", html, pos="VERB") == {"aspect_pair": "imperfective (perfective: prebrati or prebirati)"}
    (head,) = HEAD_RE.findall(html)
    assert sl_tone_fold(head.split()[0]) == "brati"


def test_a_single_aspect_chip_is_the_dictionarys_answer(verbs):
    assert _render("narediti", verbs.lookup("narediti"), pos="VERB") == {"aspect_pair": "perfective"}
    assert _render("delati", verbs.lookup("delati"), pos="VERB") == {"aspect_pair": "imperfective"}


def test_a_biaspectual_slovene_verb_prints_no_aspect_at_all(verbs):
    """A recorded limitation, not a bug: the shared hook answers from chips only when there is
    exactly ONE (two chips can be two different lexemes), and neither of the other two sources can
    help in Slovenian. wty-sl-en has 0 head lines saying "impf or pf" (measured over all 56,797
    rows), and the model gives a biaspectual verb the Vmb* tag with NO Aspect feature at all
    (``videti`` -> Vmbn VerbForm=Inf, ``sel`` -> Vmbp-sm Gender|Number|VerbForm). So a verb that is
    both aspects simply carries no aspect field, which is better than guessing one of the two.
    """
    for term in ("videti", "iti"):
        html = verbs.lookup(term)
        assert html is not None and html.count('data-category="aspect"') == 2
        assert _render(term, html, pos="VERB") == {}
        assert _render(term, html, morph="VerbForm=Inf", pos="VERB") == {}


def test_morph_still_names_both_aspects_when_a_tagger_supplies_them(verbs):
    """The hook's biaspectual path is live; it is Slovenian's tagger that never takes it."""
    assert _render("videti", verbs.lookup("videti"), morph="Aspect=Imp,Perf", pos="VERB") == {
        "aspect_pair": "imperfective or perfective"
    }


def test_a_verb_with_no_chip_and_no_head_line_falls_back_to_morph(verbs):
    """kupiti is ``v`` with nothing else: the thin dictionary, and morph is the only source left."""
    html = verbs.lookup("kupiti")
    assert _render("kupiti", html, pos="VERB") == {}
    assert _render("kupiti", html, morph="Aspect=Perf|VerbForm=Inf", pos="VERB") == {"aspect_pair": "perfective"}
