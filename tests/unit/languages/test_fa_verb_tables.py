"""Persian verb tables over the REAL hazm pack: what subtitle Persian actually writes.

The fixture tests (``test_fa_conjugation.py``, ``test_fa_tokenizer.py``) prove the
tables are built right; only the full ``words.dat`` can prove the new tiers do
not steal a noun, because tier 1 (a tagged row) and the colloquial word map are
what stand between a noun and the verb tables. Every surface below is from the
top of fa_50k (hermitdave/FrequencyWords, OpenSubtitles 2018), spelled as the
list spells it (Arabic yeh and all), so ``fa_normalize`` runs first as it does
in a real parse.

The pack is hard-required (``tests/_pack_seeds.py``); the lexicon is
module-scoped because building it costs ~4 s and ``tests/conftest.py`` clears
the tagger cache per test.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.availability import FA_DATA_COMPONENT
from anki_miner.languages.fa.script import ZWNJ
from tests._pack_seeds import seeded_component


@pytest.fixture(scope="module")
def real_lexicon():
    pack_root = seeded_component("fa", FA_DATA_COMPONENT, "words.dat").parent
    return lexicon.build(data.load(pack_root))


@pytest.fixture
def armed(real_lexicon, monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state: set it explicitly so a
    # lexicon another test file built cannot make these pass for the wrong reason.
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", real_lexicon.is_known_verb_form)
    return real_lexicon


def _one(surface: str, lexicon_: lexicon.PersianLexicon):
    tokens = [
        token
        for token in fa_tokenizer.to_duck_tokens(fa_script.fa_normalize(surface), lexicon_)
        if token.feature.pos1 != "PUNCT"
    ]
    assert len(tokens) == 1, [token.surface for token in tokens]
    return tokens[0]


@pytest.mark.parametrize(
    ("surface", "infinitive"),
    [(f"می{ZWNJ}خرم", "خریدن"), (f"می{ZWNJ}پرسد", "پرسیدن"), (f"می{ZWNJ}کشند", "کشیدن"), ("باشیم", "بودن")],
)
def test_a_formal_present_is_standard_persian_not_colloquial(surface, infinitive, armed):
    token = _one(surface, armed)
    assert token.feature.lemma == infinitive
    assert token.feature.pos2 != "informal"
    assert "Register=Informal" not in token.morph


@pytest.mark.parametrize(
    ("infinitive", "stem"),
    [("نوشتن", "نویس"), ("فروختن", "فروش"), ("بودن", "باش"), ("کشتن", "کش"), ("رفتن", "رو")],
)
def test_the_present_stem_field_shows_the_live_stem(infinitive, stem, real_lexicon):
    assert real_lexicon.present_stem(infinitive) == stem
