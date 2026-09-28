"""The Persian ladder's tiers over the REAL hazm tables: which reading a common surface gets.

Measured gaps, each on subtitle-frequent words the 300-row fixture subset never holds:

* verb-first surfaces: words.dat tags kone, kardi, beri and nadari as nouns only ("tick",
  "Kurdish", "brie", "poverty"), and tier 1 answered before any verb table (fa_50k: 185k tokens);
* the stem tier's choice: hazm strips the LONGEST suffix, so -ay/-am ate the alef of sedaye,
  babam and aqaye (sad "hundred", bab, aq) where the one-letter strip leaves the word.

The pack is hard-required and the lexicon is module-scoped, as in ``test_fa_real_data.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon, stemmer
from anki_miner.languages.fa.availability import FA_DATA_COMPONENT
from anki_miner.languages.fa.script import ZWNJ
from tests._pack_seeds import seeded_component

DATA = Path(__file__).resolve().parents[3] / "anki_miner" / "languages" / "fa" / "data"


@pytest.fixture(scope="module")
def real_lexicon():
    root = seeded_component("fa", FA_DATA_COMPONENT, "words.dat").parent
    return lexicon.build(data.load(root))


@pytest.fixture
def armed(real_lexicon, monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state: set it explicitly so a
    # lexicon another test file built cannot make these pass for the wrong reason.
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", real_lexicon.is_known_verb_form)
    return real_lexicon


def _one(word: str, lex):
    (token,) = [t for t in fa_tokenizer.to_duck_tokens(fa_script.fa_normalize(word), lex) if t.feature.pos1 != "PUNCT"]
    return token


class TestVerbFirst:
    @pytest.mark.parametrize(
        ("surface", "infinitive"),
        [
            ("کردی", "کردن"),  # "you did", not "Kurdish"
            ("کردي", "کردن"),  # the Arabic-yeh spelling cp1256 subtitles carry
            ("كردي", "کردن"),  # and the Arabic kaf
            ("نداری", "داشتن"),  # "you don't have", not "poverty"
            ("بری", "رفتن"),  # "you go", not "brie"
            ("کنه", "کردن"),  # colloquial "does", not "tick"
        ],
    )
    def test_the_verb_reading_wins_over_the_tagged_noun(self, surface, infinitive, armed):
        assert armed.tags(fa_script.fa_normalize(surface)), "the premise: words.dat tags it"
        token = _one(surface, armed)
        assert token.feature.pos1 == "V", surface
        assert token.feature.lemma == infinitive, surface

    def test_kone_reaches_the_verb_table_through_its_formal_spelling(self, armed):
        # kone is colloquial.tsv's konad, whose own tagged row ("blunt") must not
        # stop the verb table either.
        token = _one("کنه", armed)
        assert token.feature.surface_formal == "کند"
        assert token.feature.present_stem == "کن"
        # kardan is a stopwords.dat entry, so the default run drops it like every
        # other kardan form: no card, and no "tick" card either.
        assert token.feature.pos2 == "stopword"

    def test_beri_is_mined_as_raftan(self, armed):
        token = _one("بری", armed)
        assert token.feature.pos2 == "informal"
        assert token.feature.surface_formal == "بروی"

    def test_kist_is_not_on_the_list(self, armed):
        # ki + ast, no verb-table key: the list must not hold it.
        assert not armed.is_verb_first("کیست")
        assert _one("کیست", armed).feature.pos1 == "N"

    @pytest.mark.parametrize("noun", ["مرد", "داشت", "داد", "سخت", "شکست", "خواست", "کند"])
    def test_the_probe_p9_homographs_stay_nouns(self, noun, armed):
        token = _one(noun, armed)
        assert token.feature.pos1 != "V", noun
        assert token.feature.lemma == noun

    def test_every_listed_surface_has_a_verb_reading(self, armed):
        rows = [
            line.split("\t")
            for line in DATA.joinpath("verb_first.tsv").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        assert rows
        for (surface,) in rows:
            assert armed.tags(surface), f"{surface}: a row tier 1 would not answer needs no entry"
            assert _one(surface, armed).feature.pos1 == "V", surface

    def test_the_file_is_plain_utf8_with_a_provenance_header(self):
        raw = DATA.joinpath("verb_first.tsv").read_bytes()
        assert raw.startswith(b"#")
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in raw


class TestStemChoice:
    @pytest.mark.parametrize(
        ("surface", "stem"),
        [
            ("صدای", "صدا"),  # "voice of", not sad "hundred"
            ("صداش", "صدا"),
            ("بابام", "بابا"),  # "my dad", not bab
            ("آقای", "آقا"),
            ("دنیای", "دنیا"),
            ("بالای", "بالا"),
        ],
    )
    def test_the_longest_tagged_remainder_is_the_front(self, surface, stem, armed):
        assert stemmer.stem(surface) != stem, "the premise: hazm's own strip is shorter"
        assert _one(surface, armed).feature.lemma == stem

    @pytest.mark.parametrize(
        ("surface", "noun"),
        [("کارهای", "کار"), (f"کار{ZWNJ}های", "کار"), (f"کتاب{ZWNJ}ها", "کتاب")],
    )
    def test_a_plural_strip_still_answers_first(self, surface, noun, armed):
        # kar-haye also strips to kare ("worker"), which is longer and tagged.
        assert _one(surface, armed).feature.lemma == noun

    def test_the_tier_answers_no_word_hazms_own_stem_did_not(self, armed):
        # The standalone superlative: hazm strips all of -tarin, which is no row,
        # and the -in strip must not answer instead with tar "wet".
        tarin = "ترین"
        assert not armed.tags(stemmer.stem(tarin))
        assert _one(tarin, armed).feature.pos1 == "unknown"

    def test_the_ported_stemmer_itself_stays_hazm_exact(self):
        assert stemmer.stem("صدای") == "صد"
        assert stemmer.stem("بابام") == "باب"
