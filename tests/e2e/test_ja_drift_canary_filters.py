"""JA drift canary 2: the same guarantee, through the optional filters.

Canary 1 (``test_ja_drift_canary.py``) runs a ja session with every optional
filter OFF, so mutation testing found it blind to seven of the eight
LanguageProfile seams Stage 1A introduced. This file is the second canary,
added in the Stage 1A fix round; it does NOT replace the first. **Baseline 2
was captured at stage-1A fix head 50cc5d42**, it gates Stage 1B onward, and
canary 1 and its baseline (``assets/ja_drift_baseline.json``) are unchanged and
still gate. NEITHER baseline is ever regenerated to make a failing extraction
pass — revert the extraction instead. The two capture flows are deliberately
separated by env var (``ANKI_MINER_CANARY2_CAPTURE`` here, not canary 1's
``ANKI_MINER_CANARY_CAPTURE``) so re-capturing this baseline can never rewrite
that one.

What this one adds, and what each addition is here to catch (verified by
mutating the ja profile's seam objects and re-capturing — canary 1 saw NONE of
these four, this file fails on all four):

* ``exclude_hiragana_only_words`` + ``exclude_katakana_only_words`` on, with
  わかる mined but NOT whitelisted, so the script filter is the only thing
  keeping it out of the baseline (``ScriptSupport.matches``).
* A kana card front, しゃべる, whose folded reading also matches the kana-term
  homograph シャベル: the rendered definition must carry only 喋る's gloss
  (``DictKeyFolding.homograph_keep_mask``).
* A katakana card front, コーヒー, whose only dictionary row is the kanji
  headword 珈琲 stored under a KATAKANA reading — reachable only through the
  hiragana fold on both sides (``DictKeyFolding.fold_reading``).
* A noun front whose unidic lemma differs from its surface (玉子 / 卵) appearing
  on two lines, so the curator's sentence-candidate list is rebuilt through the
  profile's card-front policy (``MinedFormPolicy`` via
  ``WordFilterService._line_preserves_mined_form``).
* ``use_i_plus_one_filter`` on over a seeded known-word collection, a whitelist
  that force-includes past every coverage filter, and a curation callback whose
  recorded candidate sentences are part of the baseline.

Three seams stay unreachable from a JA canary and are deliberately not chased
here: ``ReadingSupport`` and ``SentenceAnnotator`` are injected only on the
non-ja path (``SubtitleParserService._reading_support`` is ``None`` for ja), and
``LookupStrategy`` only fires on a lookup MISS, which no fixture word has.

Always run this file as:
    .venv/bin/pytest tests/e2e/test_ja_drift_canary_filters.py -m e2e -v
A bare run deselects everything here (CLI -m replaces addopts' -m "not e2e").
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path

import pytest

from anki_miner.config.config import (
    AnkiMinerConfig,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
)
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.models.reading import ReadingDocument, ReadingUnit
from anki_miner.presenters.null_presenter import NullPresenter
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.pitch_accent import storage as pitch_storage
from tests._home_isolation import restore_home_patches, set_test_home
from tests.e2e.fake_ankiconnect import FakeAnkiConnect
from tests.e2e.fixtures_dictionary import GLOSSES
from tests.e2e.fixtures_subtitle import LEMMA_READINGS, SUBTITLE_LINES

pytestmark = pytest.mark.e2e

BASELINE_PATH = Path(__file__).parent / "assets" / "ja_drift_baseline_filters.json"

#: Distinct from canary 1's ids so the two runs can never share on-disk state.
DICT_ID = "canary2-dict"
FREQ_SOURCE_ID = "canary2-freq"
PITCH_SOURCE_ID = "canary2-pitch"
DECK = "canary2-deck"
MODEL = "canary2-model"
#: Seeded known vocabulary lives in its own deck so the capture can tell the
#: run's OWN notes apart from the collection it mined against.
KNOWN_DECK = "canary2-known"

#: Same full logical-field span as canary 1 (see its FIELD_MAP comment).
FIELD_MAP: dict[str, str] = {
    "word": "Expression",
    "sentence": "Sentence",
    "definition": "MainDefinition",
    "glossary": "Glossary",
    "picture": "Picture",
    "audio": "SentenceAudio",
    "expression_furigana": "ExpressionFurigana",
    "expression_reading": "ExpressionReading",
    "sentence_furigana": "SentenceFurigana",
    "sentence_reading": "SentenceReading",
    "pitch_position": "PitchPosition",
    "pitch_category": "PitchCategory",
    "pitch_graph": "PitchGraph",
    "pitch_text": "PitchText",
    "frequency": "Frequency",
    "frequency_sort": "FreqSort",
    "source": "Source",
    "expression_audio": "WordAudio",
}
MODEL_FIELDS = [*FIELD_MAP.values(), "IsWordAndSentenceCard"]

#: Lines appended to canary 1's fixture subtitle. ``fixtures_subtitle.py`` is a
#: pre-existing module and is NOT edited; the words this canary needs live here.
EXTRA_LINES: tuple[str, ...] = (
    # しゃべる: hiragana verb, admitted by the parser's kana-recovery seam
    # (attested through 喋る's reading), dropped by the script filter unless
    # force-included, and the homograph-mask case.
    "友達とよくしゃべる",
    # コーヒー: katakana front reachable only through the hiragana reading fold.
    # Also gives 買う a second line for the curator's sentence candidates.
    "コーヒーを買いました",
    # わかる: the control. Mined, defined, i+1-clean, NOT whitelisted — the only
    # thing keeping it out of the baseline is the script filter.
    "よくわかる",
    # 玉子 (lemma 卵) on two lines: a surface-mined noun whose card front is
    # recomputed per candidate line by the profile's MinedFormPolicy.
    "玉子を買いました",
    "玉子を食べる",
)

#: ``(term, reading, gloss)`` rows appended to canary 1's dictionary fixture.
#: ``fixtures_dictionary.py`` is pre-existing and is NOT edited. Readings are
#: written the way a real Yomitan bank writes them — katakana for 珈琲, so the
#: import-time hiragana fold is what makes the row findable at all.
EXTRA_ROWS: tuple[tuple[str, str, str], ...] = (
    ("喋る", "しゃべる", "to chat; to talk"),
    # Same folded reading as 喋る, kana term: Rule A′/B must drop it from
    # しゃべる's rendered definition.
    ("シャベル", "シャベル", "shovel"),
    ("珈琲", "コーヒー", "coffee"),
    ("分かる", "わかる", "to understand"),
    ("玉子", "たまご", "egg"),
)

#: Card fronts seeded into the fake collection as already-known vocabulary, so
#: the i+1 filter has real work: it keeps a word only when some line has exactly
#: one unknown. 美味しい / 料理 / 食べる share a line with no known word and are
#: the words i+1 drops.
KNOWN_FRONTS: tuple[str, ...] = ("新しい", "本", "今日", "学校", "友達", "公園")

#: Whitelist entries (card front and lemma for each), force-included past every
#: coverage filter INCLUDING the script filter. わかる is deliberately absent.
WHITELIST: tuple[str, ...] = ("しゃべる", "喋る", "コーヒー", "珈琲", "玉子", "卵")

#: Same four accent shapes canary 1 cycles. Committed fixture data: arbitrary,
#: but must never change.
_PITCH_PATTERNS: tuple[str, ...] = ("0", "1", "2", "3")

_GLOSS_RE = re.compile(r'<li class="gloss-item">(.*?)</li>')


def _terms() -> dict[str, str]:
    """term -> reading for every seeded dictionary row (canary 1's plus ours)."""
    terms = dict(LEMMA_READINGS)
    for term, reading, _gloss in EXTRA_ROWS:
        terms[term] = reading
    return terms


def _document() -> ReadingDocument:
    texts = [text for _start, _end, text in SUBTITLE_LINES] + list(EXTRA_LINES)
    units = [ReadingUnit(text=text, index=i, location_label=f"p.{i + 1}") for i, text in enumerate(texts)]
    return ReadingDocument(title="canary2", kind="book", series="canary2", episode="canary2", units=units, warnings=[])


def _seed_offline_dict(dicts_root: Path) -> None:
    """Seed canary 1's lemma rows plus :data:`EXTRA_ROWS` into one index.

    Written with the DEFAULT (Japanese) key folding, which is what the real
    Yomitan importer still uses — the profile's ``dict_keys`` reaches the query
    side only (``IndexedDictProvider``), so an asymmetric fold here would test
    something production does not do.
    """
    db_path = dicts_root / DICT_ID / "index.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        DictRow(
            term=lemma,
            reading=reading,
            content=f'<li class="gloss-item">{GLOSSES[lemma]}</li>',
            sequence=sequence,
        )
        for sequence, (lemma, reading) in enumerate(LEMMA_READINGS.items(), start=1)
    ]
    rows += [
        DictRow(term=term, reading=reading, content=f'<li class="gloss-item">{gloss}</li>', sequence=sequence)
        for sequence, (term, reading, gloss) in enumerate(EXTRA_ROWS, start=len(rows) + 1)
    ]
    create_index(db_path)
    bulk_insert(db_path, rows)
    write_meta(
        db_path,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": DICT_ID,
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )


def _seed_frequency_source(freqs_root: Path) -> None:
    """One deterministic frequency source over every seeded term.

    The kana/katakana fronts (しゃべる, コーヒー) are ranked under their KANJI
    terms only, so their notes come out with empty frequency fields — the
    unranked-means-empty invariant rides along in the baseline.
    """
    rows: list[freq_storage.FreqRow] = [
        (term, reading, rank, None) for rank, (term, reading) in enumerate(_terms().items(), start=1)
    ]
    freq_storage.build_index(
        freqs_root / FREQ_SOURCE_ID / "index.sqlite",
        rows,
        {
            "schema_version": str(freq_storage.SCHEMA_VERSION),
            "source_name": FREQ_SOURCE_ID,
            "format": "csv",
            "entry_count": str(len(rows)),
        },
    )


def _seed_pitch_source(pitch_root: Path) -> None:
    """One deterministic pitch source over every seeded term."""
    rows: list[pitch_storage.PitchStorageRow] = [
        (reading, term, _PITCH_PATTERNS[i % len(_PITCH_PATTERNS)], "", "")
        for i, (term, reading) in enumerate(_terms().items())
    ]
    pitch_storage.build_index(
        pitch_root / PITCH_SOURCE_ID / "index.sqlite",
        rows,
        {
            "schema_version": str(pitch_storage.SCHEMA_VERSION),
            "source_name": PITCH_SOURCE_ID,
            "format": "csv",
            "entry_count": str(len(rows)),
        },
    )


def _seed_known_collection(fake: FakeAnkiConnect) -> None:
    """Put :data:`KNOWN_FRONTS` in the collection as existing vocabulary.

    ``get_existing_vocabulary`` reads the FIRST field of every note in the
    collection, so a note only needs its Expression filled. They go in their own
    deck, which the capture then excludes.
    """
    for front in KNOWN_FRONTS:
        fake._notes[fake._next_note_id] = {
            "deckName": KNOWN_DECK,
            "modelName": MODEL,
            "fields": {name: (front if name == "Expression" else "") for name in MODEL_FIELDS},
            "tags": [],
        }
        fake._next_note_id += 1


def _config(fake: FakeAnkiConnect, whitelist_path: Path) -> AnkiMinerConfig:
    """Canary 1's config plus the optional filters it leaves off."""
    return dataclasses.replace(
        AnkiMinerConfig(),
        ankiconnect_url=fake.url,
        anki_deck_name=DECK,
        anki_note_type=MODEL,
        anki_fields=dict(FIELD_MAP),
        card_type="word_and_sentence",
        expression_audio_chain=(),
        reading_min_occurrence=1,
        use_known_words_db=False,
        deduplicate_sentences=False,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id=DICT_ID, enabled=True),),
        frequency_chain=(FreqEntry(source_id=FREQ_SOURCE_ID, enabled=True),),
        pitch_chain=(PitchSourceEntry(source_id=PITCH_SOURCE_ID, enabled=True),),
        # The four this canary exists for.
        use_i_plus_one_filter=True,
        exclude_hiragana_only_words=True,
        exclude_katakana_only_words=True,
        use_whitelist=True,
        whitelist_path=whitelist_path,
    )


def capture_payloads(tmp_home: Path) -> dict[str, list]:
    """Run one filtered ja reading session; return its notes AND curation view.

    The curation half is the curator's own input: the callback records what the
    dialog would show (front, lemma, in-episode occurrences and the alternative
    example sentences ``WordFilterService.attach_sentence_candidates`` built),
    then confirms the whole list so the run proceeds to card creation.
    """
    saved = set_test_home(tmp_home)
    try:
        _seed_offline_dict(tmp_home / "dicts")
        _seed_frequency_source(tmp_home / "freqs")
        _seed_pitch_source(tmp_home / "pitch")
        whitelist_path = tmp_home / "whitelist.txt"
        whitelist_path.write_text("\n".join(WHITELIST) + "\n", encoding="utf-8")
        curated: list[dict] = []

        def curation_callback(words: list) -> list:
            curated.extend(
                {
                    "word": word.mined_form,
                    "lemma": word.lemma,
                    "occurrences": word.occurrence_count,
                    "candidates": [candidate.sentence for candidate in word.sentence_candidates],
                }
                for word in words
            )
            return list(words)

        with FakeAnkiConnect() as fake:
            fake.seed_deck(DECK)
            fake.seed_deck(KNOWN_DECK)
            fake.seed_model(MODEL, MODEL_FIELDS)
            _seed_known_collection(fake)
            processor = create_episode_processor(_config(fake, whitelist_path), NullPresenter())
            try:
                processor.process_reading(_document(), curation_callback=curation_callback)
            finally:
                processor.close()
            notes = [
                {
                    "deckName": note["deckName"],
                    "modelName": note["modelName"],
                    "tags": sorted(note["tags"]),
                    "fields": dict(note["fields"]),
                }
                for note in fake._notes.values()
                if note["deckName"] == DECK
            ]
    finally:
        restore_home_patches(saved)
    key = lambda payload: json.dumps(payload, ensure_ascii=False, sort_keys=True)  # noqa: E731
    return {"notes": sorted(notes, key=key), "curation": sorted(curated, key=key)}


def _baseline() -> dict[str, list]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _note(baseline: dict[str, list], front: str) -> dict | None:
    return next((n for n in baseline["notes"] if n["fields"]["Expression"] == front), None)


def test_filtered_ja_payloads_match_baseline(tmp_path: Path) -> None:
    """Notes AND curator input are byte-identical to baseline 2."""
    payloads = capture_payloads(tmp_path / "home")
    if os.environ.get("ANKI_MINER_CANARY2_CAPTURE") == "1":
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(payloads, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("baseline captured; re-run without ANKI_MINER_CANARY2_CAPTURE")
    assert BASELINE_PATH.exists(), (
        f"{BASELINE_PATH} missing — capture it with ANKI_MINER_CANARY2_CAPTURE=1 "
        f".venv/bin/pytest tests/e2e/test_ja_drift_canary_filters.py -m e2e -v"
    )
    assert payloads == _baseline()


def test_the_baseline_still_carries_every_seam_it_was_built_for() -> None:
    """Each fixture word is here to catch one seam; assert it is still doing so.

    A baseline is only as good as the shape it captured. These read the
    COMMITTED file, so a re-capture that quietly lost a seam fails here even
    though the payload comparison above would pass.
    """
    baseline = _baseline()
    assert len(baseline["notes"]) >= 4
    # Field span is a UNION property here, not a per-note one: the kana fronts
    # are ranked in the frequency source under their kanji terms only, so their
    # notes carry no Frequency/FreqSort key at all. That split is the point —
    # canary 1's every-note assertion cannot see the unranked shape.
    mapped = set(FIELD_MAP.values())
    written = {name for note in baseline["notes"] for name in note["fields"]}
    assert mapped <= written, sorted(mapped - written)
    ranked = [n for n in baseline["notes"] if n["fields"].get("Frequency")]
    assert ranked and len(ranked) < len(baseline["notes"])

    # ScriptSupport: わかる is mined, defined and i+1-clean; only the script
    # filter keeps it out.
    assert _note(baseline, "わかる") is None

    # homograph_keep_mask: しゃべる's folded reading also matches the kana term
    # シャベル, whose gloss must not reach the card.
    shaberu = _note(baseline, "しゃべる")
    assert shaberu is not None
    assert _GLOSS_RE.findall(shaberu["fields"]["MainDefinition"]) == ["to chat; to talk"]

    # fold_reading: コーヒー has no term row at all — 珈琲 is reachable only by
    # folding its katakana reading on both sides.
    coffee = _note(baseline, "コーヒー")
    assert coffee is not None
    assert _GLOSS_RE.findall(coffee["fields"]["MainDefinition"]) == ["coffee"]

    # MinedFormPolicy: the surface-mined noun 玉子 (lemma 卵) keeps both of its
    # lines as curator sentence candidates, which is decided by recomputing the
    # card front per line through the profile's policy.
    tamago = next(c for c in baseline["curation"] if c["word"] == "玉子")
    assert tamago["lemma"] == "卵"
    assert len(tamago["candidates"]) == 2

    # i+1: the line with no known word contributes nobody.
    fronts = {note["fields"]["Expression"] for note in baseline["notes"]}
    assert not fronts & {"美味しい", "料理", "食べる"}
