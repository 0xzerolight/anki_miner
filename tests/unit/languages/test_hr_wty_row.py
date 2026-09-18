"""Real wty-sh-en rows survive import + render under the Croatian keys; the hook prints gender and aspect."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.hr.morphology import hr_tone_fold
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import declares_other_language, import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hr"
ROW = json.loads((FIXTURES / "wty_row.json").read_text(encoding="utf-8"))
TAGS = json.loads((FIXTURES / "tag_bank_sample.json").read_text(encoding="utf-8"))
CYRILLIC_KNJIGA = "\u043a\u045a\u0438\u0433\u0430"


def _provider(tmp_path, fixture, name):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(fixture["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(fixture["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(fixture["term_rows"]))
    import_yomitan_zip(archive, tmp_path / name, dict_id="wty-sh-en", language="hr")
    loaded = IndexedDictProvider(
        "wty-sh-en", tmp_path / name / "wty-sh-en" / "index.sqlite", keys=get_profile("hr").dict_keys
    )
    assert loaded.load()
    return loaded


@pytest.fixture
def provider(tmp_path):
    return _provider(tmp_path, ROW, "dicts")


@pytest.fixture
def verbs(tmp_path):
    return _provider(tmp_path, TAGS, "verbs")


def test_the_fixture_carries_its_licence_and_declares_serbo_croatian():
    assert "CC BY-SA 4.0" in ROW["license"] and ROW["index"]["sourceLanguage"] == "sh"


def test_an_hr_profile_accepts_the_sh_section_and_nothing_else():
    """R28: the sh tree IS hr's dictionary; a Serbian-declared zip is still a mismatch."""
    assert declares_other_language("sh", "hr", "sh") is False
    assert declares_other_language("hr", "hr", "sh") is False
    assert declares_other_language("sr", "hr", "sh") is True
    assert declares_other_language("", "hr", "sh") is False


@pytest.mark.parametrize("term", sorted(ROW["rendered_html"]))
def test_every_row_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == ROW["rendered_html"][term]


def test_the_head_line_carries_tone_marks_the_fold_removes(provider):
    """D2 strips the marks before the hook reads the gender letter; the fold recovers the plain headword."""
    html = provider.lookup("knjiga")
    assert html is not None
    (head,) = re.findall(r'data-sc-content="Grammar-content">([^<]*)<', html)
    headword, gender = head.split()[:2]
    assert headword != "knjiga" and hr_tone_fold(headword) == "knjiga"
    assert gender == "f" and "Cyrillic spelling" in head


def _render(term: str, html: str | None, morph: str = "", pos: str = "NOUN") -> dict[str, str]:
    hook = get_profile("hr").render_hooks[1]
    word = SimpleNamespace(pos=pos, mined_form=term, surface=term, morph=morph, definition_html=html or "")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_gender_hook_reads_the_real_head_line(provider):
    assert _render("knjiga", provider.lookup("knjiga")) == {"noun_gender": "feminine"}
    assert _render("stol", provider.lookup("stol")) == {"noun_gender": "masculine"}
    assert _render("more", provider.lookup("more")) == {"noun_gender": "neuter"}


def test_an_animacy_qualifier_does_not_hide_the_gender(provider):
    """The head line is ``p\u0061\u0301s m anim``: the seam skips the qualifier, and hr declares no animacy labels."""
    assert _render("pas", provider.lookup("pas")) == {"noun_gender": "masculine"}


def test_the_aspect_pair_comes_from_the_head_line_with_the_tone_fold(provider):
    """The partner is written with tone marks the card must not print: pro\u010d\u0069\u0300tati -> pro\u010ditati."""
    assert _render("\u010ditati", provider.lookup("\u010ditati"), pos="VERB") == {
        "aspect_pair": "imperfective (perfective: pro\u010ditati)"
    }
    assert _render("pro\u010ditati", provider.lookup("pro\u010ditati"), pos="VERB") == {
        "aspect_pair": "perfective (imperfective: \u010ditati)"
    }


def test_a_biaspectual_verb_prints_both_aspects(verbs):
    assert _render("biti", verbs.lookup("biti"), pos="VERB") == {"aspect_pair": "imperfective or perfective"}
    assert _render("kupiti", verbs.lookup("kupiti"), pos="VERB") == {"aspect_pair": "perfective"}


def test_the_cyrillic_spelling_never_becomes_a_card_front(provider):
    """It is a term of the sh dictionary and it renders, but no Croatian query reaches it and it fronts nothing."""
    assert provider.lookup(CYRILLIC_KNJIGA) == ROW["rendered_html"][CYRILLIC_KNJIGA]
    html = provider.lookup("knjiga")
    assert html is not None and "Cyrillic spelling" in html  # only inside the entry, as the head line's note
    assert any("\u0400" <= char <= "\u04ff" for char in html)
    profile = get_profile("hr")
    assert profile.lookup.candidates("knjiga", "Knjige", None) == [("Knjige", 0), ("knjige", 0)]
    assert not profile.script.contains_target_script(CYRILLIC_KNJIGA)


def test_an_inflected_form_row_reaches_its_lemma(provider):
    html = provider.lookup("knjige")
    assert html is not None and "knjiga" in html
