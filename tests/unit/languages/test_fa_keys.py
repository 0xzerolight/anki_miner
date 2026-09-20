"""One fold, one function: Persian dictionary keys, frequency keys and card dedup (S3, S4, R6/R7).

Persian spells the same word several ways — with or without the ZWNJ, with the
Farsi yeh or the Arabic one, with or without the ezafe hamza — and every one of
them has to reach the same dictionary row and produce the same card. The rows
below are verbatim ``wty-fa-en`` records (``tests/fixtures/fa/wty_rows.json``),
imported through the real Yomitan importer, so the fold is exercised where it
actually runs rather than called directly.

Every Persian literal is a ``\\N{NAME}`` escape or built from one: a raw RTL run
reorders on screen inside a source line, and the ZWNJ is invisible.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages.fa.script import ZWNJ, fa_fold
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
WTY = json.loads((FIXTURES / "wty_rows.json").read_text(encoding="utf-8"))

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_SEEN = "\N{ARABIC LETTER SEEN}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_NOON = "\N{ARABIC LETTER NOON}"
_ZAIN = "\N{ARABIC LETTER ZAIN}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_HEH = "\N{ARABIC LETTER HEH}"
_KHAH = "\N{ARABIC LETTER KHAH}"
_FARSI_YEH = "\N{ARABIC LETTER FARSI YEH}"
_ARABIC_YEH = "\N{ARABIC LETTER YEH}"
_HAMZA_ABOVE = "\N{ARABIC HAMZA ABOVE}"

#: ketab, "book".
KETAB = _KEHEH + _TEH + _ALEF + _BEH
#: xane, "house".
XANE = _KHAH + _ALEF + _NOON + _HEH
#: zist-shenasi, "biology" — a headword whose OWN spelling carries the ZWNJ.
ZIST = _ZAIN + _FARSI_YEH + _SEEN + _TEH
SHENASI = _SHEEN + _NOON + _ALEF + _SEEN + _FARSI_YEH
BIOLOGY = ZIST + ZWNJ + SHENASI


@pytest.fixture(scope="module")
def provider(tmp_path_factory) -> IndexedDictProvider:
    tmp = tmp_path_factory.mktemp("wty_fa")
    archive = tmp / "wty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(WTY["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(WTY["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(WTY["term_rows"]))
    import_yomitan_zip(archive, tmp / "dicts", dict_id="wty-fa-en", language="fa")
    found = IndexedDictProvider(
        "wty-fa-en", tmp / "dicts" / "wty-fa-en" / "index.sqlite", keys=get_profile("fa").dict_keys
    )
    assert found.load()
    return found


class TestDictionaryKeySymmetry:
    def test_a_zwnj_headword_answers_a_zwnj_less_query(self, provider):
        """The spelling a learner types is rarely the spelling the editor stored."""
        with_joiner = provider.lookup(BIOLOGY)
        assert with_joiner
        assert provider.lookup(ZIST + SHENASI) == with_joiner

    def test_a_zwnj_less_query_is_not_a_different_word(self, provider):
        """The reverse direction: the index key is folded on BOTH sides, once."""
        assert fa_fold(BIOLOGY) == fa_fold(ZIST + SHENASI)

    def test_an_arabic_yeh_query_reaches_a_farsi_yeh_headword(self, provider):
        """cp1256 has no Farsi yeh, so every legacy file spells it the Arabic way."""
        arabic_spelling = (ZIST + ZWNJ + SHENASI).replace(_FARSI_YEH, _ARABIC_YEH)
        assert arabic_spelling != BIOLOGY
        assert provider.lookup(arabic_spelling) == provider.lookup(BIOLOGY)

    def test_an_ezafe_hamza_query_reaches_the_bare_heh(self, provider):
        """xane-ye, the ezafe form a subtitle writes, is the same headword."""
        assert provider.lookup(XANE + _HAMZA_ABOVE) == provider.lookup(XANE)

    def test_the_index_is_not_folding_everything_together(self, provider):
        """The fold must not be so lossy that two real words collide."""
        assert provider.lookup(KETAB) != provider.lookup(XANE)


class TestFoldIdempotence:
    """S4's converter pin, adapted to the in-app path: the key of a key is the key.

    Run over every committed Persian string this branch ships — the ``words.dat``
    subset, both TSV tables and the token corpus — rather than a handful of
    examples, because a fold that moves on the second pass is a whole-index bug.
    """

    @staticmethod
    def _every_committed_string() -> list[str]:
        words = [
            line.split("\t")[0] for line in (FIXTURES / "words.dat").read_text(encoding="utf-8").splitlines() if line
        ]
        data_dir = Path(__file__).resolve().parents[3] / "anki_miner" / "languages" / "fa" / "data"
        for name in ("colloquial.tsv", "compound_verbs.tsv"):
            for line in (data_dir / name).read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#"):
                    words.extend(line.split("\t")[:2])
        for line in (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines():
            if line:
                words.extend(token["surface"] for token in json.loads(line)["tokens"])
        return [word for word in words if word]

    def test_the_fold_is_idempotent_over_every_committed_string(self):
        strings = self._every_committed_string()
        assert len(strings) > 1_000
        moved = [word for word in strings if fa_fold(fa_fold(word)) != fa_fold(word)]
        assert moved == []

    def test_the_dictionary_fold_is_idempotent_too(self):
        fold = get_profile("fa").dict_keys.fold_term
        moved = [word for word in self._every_committed_string() if fold(fold(word)) != fold(word)]
        assert moved == []

    def test_the_two_folds_are_the_same_function_for_persian(self):
        """R7 lets them differ; Persian has no article to strip, so they do not."""
        profile = get_profile("fa")
        assert profile.dedup_fold is fa_fold
        for word in (BIOLOGY, KETAB, XANE):
            assert profile.dict_keys.fold_term(word) == fa_fold(word)


class TestCardDedup:
    """Three spellings of one verb make ONE card (the spec's headline fixture).

    The known set arrives already folded (the known-words DB and the Anki
    vocabulary boundary both fold on write); ``filter_unknown`` folds each probe
    with the profile's ``dedup_fold``. Both halves have to be Persian's fold or
    the same word mines twice.
    """

    @staticmethod
    def _word(surface: str, mined: str):
        from anki_miner.models.word import TokenizedWord

        return TokenizedWord(
            surface=surface,
            lemma=mined,
            reading="",
            sentence=surface,
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            mined_form_override=mined,
        )

    @staticmethod
    def _filter():
        from anki_miner.config import AnkiMinerConfig
        from anki_miner.languages.switching import switch_language
        from anki_miner.services.word_filter import WordFilterService

        profile = get_profile("fa")
        return WordFilterService(
            switch_language(AnkiMinerConfig(), "fa"),
            dedup_fold=profile.dedup_fold,
            mined_form=profile.mined_form,
            script=profile.script,
            expression_tracks_surface=lambda _word: False,
            sentence_annotation=False,
        )

    def test_an_existing_zwnj_less_card_blocks_the_zwnj_spelling(self):
        from anki_miner.services.anki_note_builder import _strip_for_dedup

        mi = "\N{ARABIC LETTER MEEM}" + _FARSI_YEH
        ravam = "\N{ARABIC LETTER REH}" + "\N{ARABIC LETTER WAW}" + "\N{ARABIC LETTER MEEM}"
        existing_front = mi + ravam  # what a legacy card stores
        mined = mi + ZWNJ + ravam  # what this app mines
        assert existing_front != mined
        # The Anki boundary strips markup and folds; that folded value is the set.
        known = {fa_fold(_strip_for_dedup(f"<b>{existing_front}</b>"))}
        remaining = self._filter().filter_unknown([self._word(mined, mined)], known)
        assert remaining == []

    def test_the_spaced_spelling_is_the_same_card_too(self):
        mi = "\N{ARABIC LETTER MEEM}" + _FARSI_YEH
        ravam = "\N{ARABIC LETTER REH}" + "\N{ARABIC LETTER WAW}" + "\N{ARABIC LETTER MEEM}"
        assert fa_fold(mi + " " + ravam).replace(" ", "") == fa_fold(mi + ZWNJ + ravam)

    def test_a_different_word_still_mines(self):
        """The guard on the one above: the fold must block a duplicate, not everything."""
        known = {fa_fold(KETAB)}
        remaining = self._filter().filter_unknown([self._word(XANE, XANE)], known)
        assert [word.mined_form for word in remaining] == [XANE]
