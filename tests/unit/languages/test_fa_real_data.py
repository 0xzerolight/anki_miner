"""Persian over the REAL hazm tables, not the committed 300-row subsets.

Two things only this file can prove: that the subset every other fa test runs on
is a faithful stand-in for the pack (same answers, both ways), and that the
engine holds up on vocabulary the subset never saw.

The pack is hard-required. ``tests/_pack_seeds.py`` fails the test when the seed
is absent rather than skipping it: a real-data test that silently skipped would
prove nothing. Building the tables costs ~4 s, so the lexicon is module-scoped
(``tests/conftest.py`` clears the tagger cache per test).
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

from anki_miner.languages.fa import FA_SMOKE_SENTENCE
from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.availability import FA_DATA_COMPONENT
from anki_miner.languages.fa.morphology import PersianMinedForm
from anki_miner.languages.fa.script import ZWNJ
from anki_miner.languages.registry import get_profile
from tests._pack_seeds import seeded_component

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"


def _corpus() -> list[dict]:
    lines = (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _frequent_verbs() -> list[tuple[str, str, int]]:
    rows = []
    for line in (FIXTURES / "frequent_verbs.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        surface, infinitive, rank = line.split("\t")
        rows.append((surface, infinitive, int(rank)))
    return rows


@pytest.fixture(scope="module")
def pack_root() -> Path:
    """The seeded pack directory, or a failure naming the command that seeds it."""
    return seeded_component("fa", FA_DATA_COMPONENT, "words.dat").parent


@pytest.fixture(scope="module")
def real_lexicon(pack_root):
    return lexicon.build(data.load(pack_root))


@pytest.fixture
def armed(real_lexicon, monkeypatch):
    # FA_SEPARATE_MI_HOOK is module-level mutable state: set it explicitly so a
    # lexicon another test file built cannot make these pass for the wrong reason.
    monkeypatch.setattr(fa_script, "FA_SEPARATE_MI_HOOK", real_lexicon.is_known_verb_form)
    return real_lexicon


def _content(tokens):
    return [token for token in tokens if token.feature.pos1 != "PUNCT"]


def test_the_pack_carries_every_table_the_manifest_declares(pack_root):
    from anki_miner.languages.fa.pack import PACK

    (component,) = PACK.components
    for sentinel in component.sentinels:
        assert (pack_root / sentinel).is_file(), sentinel


def test_the_real_word_table_is_three_orders_larger_than_the_subset(pack_root):
    """The floor that makes every other assertion here mean something.

    ``verbs.dat`` is committed IN FULL (13 KB), so the verb table is identical
    on both; ``words.dat`` is 3.4 MB and is the half the subset stands in for.
    """
    real = data.load(pack_root)
    subset = data.load(FIXTURES)
    assert len(subset.words) == 300
    # Measured 193,350 rows over hazm 0.12.1; a floor, so a better table is not
    # a red test and a lost one is.
    assert len(real.words) >= 190_000


def test_most_real_word_rows_carry_no_tag(pack_root):
    """The premise the ladder's tier order rests on (``tokenizer._classify``).

    158,034 of the 193,350 rows are bare ``0``. An untagged row answering before
    the stemmer would bury the word inside it, so the stemmer goes first.
    """
    real = data.load(pack_root)
    untagged = sum(1 for tags in real.words.values() if not tags)
    assert untagged / len(real.words) > 0.75


def test_an_untagged_row_whose_stem_is_tagged_mines_as_the_stem(armed):
    """The regression this file caught: ketab-ha ("books") is an untagged row.

    With the untagged tier first it came back ``unknown``, which the default
    ``allowed_pos`` drops - one of the commonest words in any Persian text,
    unmineable. 363 of the 5,000 commonest fa_50k tokens were in that state.
    """
    # The ZWNJ is the named escape: this file carries no invisible character.
    plural = f"کتاب{ZWNJ}ها"
    assert armed.tags(plural) == ()
    assert armed.is_attested(plural)
    (token,) = _content(fa_tokenizer.to_duck_tokens(plural, armed))
    assert token.feature.pos1 == "N"
    assert token.feature.lemma == "کتاب"


class TestCorpusParity:
    """Every committed corpus row gives the SAME answer on the full tables.

    This is what licenses the 300-row ``words.dat`` subset: if the two ever
    diverged, every other fa test would be measuring the fixture, not Persian.
    """

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_the_line_normalises_to_its_stored_spelling(self, row, armed):
        assert fa_script.fa_normalize(row["line"]) == row["normalized"], row["note"]

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_every_token_matches_the_corpus(self, row, armed):
        produced = _content(fa_tokenizer.to_duck_tokens(row["normalized"], armed))
        assert len(produced) == len(row["tokens"]), [token.surface for token in produced]
        for token, expected in zip(produced, row["tokens"], strict=True):
            assert token.surface == expected["surface"], row["note"]
            assert token.feature.lemma == expected["lemma"], token.surface
            assert token.feature.pos1 == expected["pos1"], token.surface
            assert token.feature.pos2 == expected["pos2"], token.surface

    @pytest.mark.parametrize("row", _corpus(), ids=lambda row: row["note"][:40])
    def test_the_mined_form_matches_the_corpus(self, row, armed):
        policy = PersianMinedForm()
        produced = _content(fa_tokenizer.to_duck_tokens(row["normalized"], armed))
        for token, expected in zip(produced, row["tokens"], strict=True):
            mined = policy.mined_form(token.feature.pos1, token.feature.lemma, token.feature.lemma, token.surface)
            assert mined == expected["mined"], token.surface
            # A past#present pair is a table key, never a card front.
            assert "#" not in mined


class TestTheSmokeLine:
    def test_the_profile_ships_the_line_this_file_parses(self):
        assert get_profile("fa").smoke_sentence == FA_SMOKE_SENTENCE

    def test_it_parses_to_six_content_tokens_ending_in_the_infinitive(self, armed):
        tokens = _content(fa_tokenizer.to_duck_tokens(fa_script.fa_normalize(FA_SMOKE_SENTENCE), armed))
        assert [token.surface for token in tokens] == FA_SMOKE_SENTENCE[:-1].split()
        verb = tokens[-1]
        assert verb.feature.pos1 == "V"
        # The prefixed present tense resolves to the infinitive, which is the card front.
        assert verb.feature.lemma == _corpus()[0]["tokens"][-1]["lemma"]


class TestTheRegisterGap:
    """The risk the spec names: 693 formal verbs and 66 informal stems against real speech.

    ``frequent_verbs.tsv`` is every V token in the first 200 rows of the real
    frequency list, with the infinitive the pack resolves it to. Nine are
    colloquial spellings and four carry the Arabic yeh, so the file exercises
    ``colloquial.tsv`` and ``fa_normalize`` as well as ``verbs.dat``.
    """

    @pytest.mark.parametrize("surface,infinitive,rank", _frequent_verbs(), ids=lambda value: str(value))
    def test_a_frequent_verb_form_resolves_to_its_infinitive(self, surface, infinitive, rank, armed):
        tokens = _content(fa_tokenizer.to_duck_tokens(fa_script.fa_normalize(surface), armed))
        assert len(tokens) == 1, [token.surface for token in tokens]
        assert tokens[0].feature.pos1 == "V", f"rank {rank}"
        assert tokens[0].feature.lemma == infinitive, f"rank {rank}"

    def test_the_count_is_a_floor_not_a_pin(self):
        """A better dictionary must not turn this file red; a lost table must."""
        assert len(_frequent_verbs()) >= 25


def test_building_the_real_tables_stays_within_its_memory_budget(pack_root):
    """Measured 41 MB retained / 54 MB peak; the ceiling leaves room, not licence.

    The tables are never released - ``tagger_provider`` evicts only on a language
    switch - so this is what a Persian session carries from its first parse.
    """
    tracemalloc.start()
    try:
        built = lexicon.build(data.load(pack_root))
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert built.verb_count > 0
    assert retained < 80 * 1024 * 1024, retained
    assert peak < 100 * 1024 * 1024, peak
