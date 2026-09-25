"""S3: one fold, applied symmetrically at every comparison site (property test).

Every stored form and every probe of the same word in another case or
composition must meet, at each site, under the same fold — and with
``dedup_fold=None`` each site keeps its pre-seam comparison exactly.
"""

from __future__ import annotations

import ast
import dataclasses
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

import anki_miner
from anki_miner.models import TokenizedWord
from anki_miner.services.anki_note_builder import _strip_for_dedup
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.word_filter import WordFilterService, folded_pairs, whitelist_hits
from anki_miner.services.word_list_service import WordListService
from tests.unit.languages.stub_registry import register_stub_profile


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


PAIRS = [
    ("Hund", "hund"),
    ("hund", "HUND"),
    ("Straße", "STRASSE"),
    ("Café", "CAFÉ"),
    ("École", "école"),
]


class _AnyScript:
    def filter_options(self):
        return ()

    def matches(self, option_id, form):
        return False

    def contains_target_script(self, text):
        return True


def _word(form: str) -> TokenizedWord:
    return TokenizedWord(
        surface=form,
        lemma=form,
        reading="",
        sentence=form,
        start_time=0,
        end_time=1,
        duration=1,
        mined_form_override=form,
    )


@pytest.mark.parametrize(("stored", "probe"), PAIRS)
def test_known_words_db(stored, probe, tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "zh", dedup_fold=_fold)
    db = KnownWordDB(tmp_path / "k.db", language="zh")
    db.initialize()
    db.add_words({stored})
    assert db.normalize_key(probe) in db.get_known_words()


@pytest.mark.parametrize(("stored", "probe"), PAIRS)
def test_anki_vocabulary_and_the_filter(stored, probe, test_config):
    service = AnkiService(test_config, script=_AnyScript(), dedup_fold=_fold)
    with patch("anki_miner.services.anki_service.post_action") as post:
        post.side_effect = [[1], [{"fields": {"Expression": {"value": f"<b>{stored}</b>", "order": 0}}}]]
        vocabulary = service._collect_first_field_forms("deck:x")

    assert WordFilterService(test_config, dedup_fold=_fold).filter_unknown([_word(probe)], vocabulary) == []
    assert service._dedup_key(stored) == service._dedup_key(probe)


@pytest.mark.parametrize(("stored", "probe"), PAIRS)
def test_word_lists_and_whitelist_coverage(stored, probe, tmp_path):
    listed = tmp_path / "list.txt"
    listed.write_text(stored + "\n", encoding="utf-8")
    service = WordListService(blacklist_path=listed, whitelist_path=listed, dedup_fold=_fold)
    service.load()

    assert service.is_blacklisted(probe) and service.is_whitelisted(probe)
    assert whitelist_hits(folded_pairs([(probe, probe)], _fold), service) == service.whitelist_entries()


def test_the_profile_fold_reaches_the_anki_service(test_config, monkeypatch):
    """``dedup_fold=None`` resolves the configured language's fold, like ``script``."""
    register_stub_profile(monkeypatch, "zh", dedup_fold=_fold)
    service = AnkiService(dataclasses.replace(test_config, language="zh"), script=_AnyScript())
    assert service._dedup_key("<b>Hund</b>") == "hund"


def test_every_anki_service_dedup_site_goes_through_the_fold():
    source = (Path(anki_miner.__file__).parent / "services" / "anki_service.py").read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_strip_for_dedup"
    ]
    assert len(calls) == 1  # only inside _dedup_key


# --- dedup_fold=None keeps every site's pre-seam comparison -------------------


def test_none_keeps_the_anki_key_and_raw_membership(test_config, tmp_path):
    service = AnkiService(test_config, script=_AnyScript())
    assert service._dedup_fold is None
    assert service._dedup_key("<b>猫</b>") == _strip_for_dedup("<b>猫</b>")
    unknown = WordFilterService(test_config).filter_unknown([_word("Hund")], {"hund"})
    assert [w.mined_form for w in unknown] == ["Hund"]

    listed = tmp_path / "list.txt"
    listed.write_text("Hund\n", encoding="utf-8")
    words = WordListService(blacklist_path=listed)
    words.load()
    assert words.is_blacklisted("Hund") and not words.is_blacklisted("hund")

    assert list(folded_pairs([("A", "B")], None)) == [("A", "B")]
