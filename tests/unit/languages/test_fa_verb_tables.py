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

from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.availability import FA_DATA_COMPONENT
from tests._pack_seeds import seeded_component


@pytest.fixture(scope="module")
def real_lexicon():
    pack_root = seeded_component("fa", FA_DATA_COMPONENT, "words.dat").parent
    return lexicon.build(data.load(pack_root))


@pytest.mark.parametrize(
    ("infinitive", "stem"),
    [("نوشتن", "نویس"), ("فروختن", "فروش"), ("بودن", "باش"), ("کشتن", "کش"), ("رفتن", "رو")],
)
def test_the_present_stem_field_shows_the_live_stem(infinitive, stem, real_lexicon):
    assert real_lexicon.present_stem(infinitive) == stem
