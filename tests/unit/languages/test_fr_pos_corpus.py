"""French mining over self-written sentences through the REAL parser and model (A.5 Stage 3 fixtures)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.fr.morphology import FR_ALLOWED_POS, FR_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "fr" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


#: Module-scoped: the autouse conftest fixture clears tagger_provider's cache around every test, so a
#: function-scoped parser would reload the spaCy model per record (~1 s each). The held objects survive it.
@pytest.fixture(scope="module")
def tagger():
    return get_tagger("fr")


@pytest.fixture(scope="module")
def parser(tagger):
    parser = get_profile("fr").create_parser(switch_language(AnkiMinerConfig(), "fr"))
    assert parser.tagger is tagger
    return parser


def _words(parser, sentence: str, **kwargs):
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], False, **kwargs
    )
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"], subtitle_cleanup=record.get("subtitle_cleanup", False))
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert all(word.sentence[word.surface_start : word.surface_end] == word.surface for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    """NBSP/NNBSP tokens are SPACE and dropped, so compare against the line without any whitespace."""
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == "".join(record["sentence"].split())


def test_the_model_never_emits_a_fine_tag(tagger):
    """F1: FR_EXCLUDED_SUBTYPES is empty because pos2 is always empty, not by default."""
    seen = {token.feature.pos2 for record in RECORDS for token in tagger(record["sentence"])}
    assert seen == {""}
    assert FR_EXCLUDED_SUBTYPES == () and FR_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB")


def test_no_break_spaces_survive_in_the_stored_book_sentence(parser):
    """The tagger reads them folded, one character for one, so the offsets still slice the stored line."""
    (word,) = [w for w in _words(parser, "Attention\u202f: le train part\u00a0!") if w.mined_form == "train"]
    assert word.sentence == "Attention\u202f: le train part\u00a0!"
    assert word.sentence[word.surface_start : word.surface_end] == "train"


def _srt(tmp_path: Path, cue: str, name: str = "fr.srt") -> Path:
    path = tmp_path / name
    path.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{cue}\n", encoding="utf-8")
    return path


def test_subtitle_sentences_keep_no_break_spaces(parser, tmp_path):
    words = parser.parse_subtitle_file(_srt(tmp_path, "Où est la gare\u202f? Ici\u00a0!"))
    assert words and all(word.sentence == "Où est la gare\u202f? Ici\u00a0!" for word in words)
    # The tagger reads the folded line: the same fronts as the line typed with plain spaces.
    plain = parser.parse_subtitle_file(_srt(tmp_path, "Où est la gare ? Ici !", "plain.srt"))
    assert {word.mined_form for word in words} == {word.mined_form for word in plain} >= {"gare"}


def test_the_bold_offsets_survive_a_no_break_space(parser, tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "config", dataclasses.replace(parser.config, bold_target_in_sentence=True))
    (word,) = [
        w for w in parser.parse_subtitle_file(_srt(tmp_path, "Où est la gare\u202f? Ici\u00a0!")) if w.surface == "gare"
    ]
    assert word.sentence_bolded == "Où est la <b>gare</b>\u202f? Ici\u00a0!"


def test_a_speaker_label_before_a_no_break_space_is_stripped(parser, tmp_path):
    words = parser.parse_subtitle_file(_srt(tmp_path, "JEAN\u202f: Où est la gare\u202f?"))
    assert words and all(word.sentence == "Où est la gare\u202f?" for word in words)


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    (word,) = [w for w in _words(parser, "LE CHAT DORT SUR LA CHAISE.") if w.mined_form == "chaise"]
    assert (word.surface, word.pos) == ("CHAISE", "NOUN")
    assert word.sentence[word.surface_start : word.surface_end] == "CHAISE"


def test_the_french_sdh_default_strips_cues_before_tagging(parser):
    """FR-1: the speaker label with French spacing never reaches the tagger (it would mine NOUN narrateur)."""
    words = _words(parser, "NARRATEUR : [porte qui claque] Où es-tu allé ? - Je suis resté ici.", subtitle_cleanup=True)
    fronts = {word.mined_form for word in words}
    assert {"aller", "rester"} <= fronts
    assert not {"narrateur", "porte", "claquer"} & fronts
    assert all("NARRATEUR" not in word.sentence and "[" not in word.sentence for word in words)


def test_the_gender_hook_reads_the_parsed_morph(parser):
    (chaise,) = [w for w in _words(parser, "La chaise est cassée.") if w.mined_form == "chaise"]
    (chat,) = [w for w in _words(parser, "Le chat dort.") if w.mined_form == "chat"]
    (hook,) = [h for h in get_profile("fr").render_hooks if isinstance(h, GrammarTagHook)]
    assert hook.render(chaise, config=AnkiMinerConfig()) == {"noun_gender": "la"}
    assert hook.render(chat, config=AnkiMinerConfig()) == {"noun_gender": "le"}


def test_known_word_fronts_fold_to_mined_fronts(parser):
    fold = get_profile("fr").dedup_fold
    assert fold is not None
    fronts = {word.mined_form for word in _words(parser, "L'homme se lève et regarde la maison.")}
    assert {fold("l’homme"), fold("se lever"), fold("la maison")} <= {fold(front) for front in fronts}
