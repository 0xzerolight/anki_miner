"""JA drift canary: the note payloads a ja run writes must never change.

Captured on the Stage-1 base commit and byte-compared after every extraction
commit of Stage 1A/1B. A diff means an "invisible" refactor changed JA output.
The baseline is NEVER regenerated to make a failing extraction pass — revert
the extraction instead.

Reading path on purpose: it exercises phases 1/2/4/5 and the real AnkiService
payload builder with no ffmpeg, no video and no network, so it is cheap enough
to re-run per commit.

Always run this file as:
    .venv/bin/pytest tests/e2e/test_ja_drift_canary.py -m e2e -v
A bare run deselects everything here (CLI -m replaces addopts' -m "not e2e").
"""

from __future__ import annotations

import dataclasses
import json
import os
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
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.pitch_accent import storage as pitch_storage
from tests._home_isolation import restore_home_patches, set_test_home
from tests.e2e.fake_ankiconnect import FakeAnkiConnect
from tests.e2e.fixtures_dictionary import DEFAULT_DICT_ID, seed_offline_dict
from tests.e2e.fixtures_subtitle import LEMMA_READINGS, SUBTITLE_LINES

pytestmark = pytest.mark.e2e

BASELINE_PATH = Path(__file__).parent / "assets" / "ja_drift_baseline.json"

#: Every logical anki_fields key mapped to a distinct Anki field name, so the
#: baseline spans the full surface phase 5 can write — including the ones the
#: default config leaves unmapped (pitch_*, frequency*, glossary, source,
#: expression_reading, sentence_reading, expression_audio).
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
DECK = "canary-deck"
MODEL = "canary-model"

#: Seeded resource ids. The chain pins exactly these three so the run consults
#: the fixture data and nothing else — the default dictionary_chain points at
#: jmdict-english, which is absent here, and would leave every word without a
#: definition (phase 5 skips those), producing an empty, assertion-free baseline.
FREQ_SOURCE_ID = "canary-freq"
PITCH_SOURCE_ID = "canary-pitch"

#: Deterministic pitch patterns, one per fixture lemma, cycled over the four
#: accent shapes so the baseline covers 平板 / 頭高 / 中高 / 尾高 rendering.
#: Committed fixture data: the values are arbitrary but must never change.
_PITCH_PATTERNS: tuple[str, ...] = ("0", "1", "2", "3")


def _document() -> ReadingDocument:
    units = [
        ReadingUnit(text=text, index=i, location_label=f"p.{i + 1}")
        for i, (_start, _end, text) in enumerate(SUBTITLE_LINES)
    ]
    return ReadingDocument(title="canary", kind="book", series="canary", episode="canary", units=units, warnings=[])


def _seed_frequency_source(freqs_root: Path) -> None:
    """Seed one deterministic frequency source covering every fixture lemma.

    Without it ``frequency`` / ``frequency_sort`` are never written and the
    mapped-field coverage assertion below cannot be satisfied. Ranks are the
    1-based fixture order, which is stable because ``LEMMA_READINGS`` is an
    ordered literal.
    """
    rows: list[freq_storage.FreqRow] = [
        (term, reading, rank, None) for rank, (term, reading) in enumerate(LEMMA_READINGS.items(), start=1)
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
    """Seed one deterministic pitch source covering every fixture lemma.

    Without it ``pitch_position`` / ``pitch_category`` / ``pitch_graph`` /
    ``pitch_text`` are never written.
    """
    rows: list[pitch_storage.PitchStorageRow] = [
        (reading, term, _PITCH_PATTERNS[i % len(_PITCH_PATTERNS)], "", "")
        for i, (term, reading) in enumerate(LEMMA_READINGS.items())
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


def capture_payloads(tmp_home: Path) -> list[dict]:
    """Run one ja reading session and return its note payloads, sorted."""
    saved = set_test_home(tmp_home)
    try:
        seed_offline_dict(tmp_home / "dicts")
        _seed_frequency_source(tmp_home / "freqs")
        _seed_pitch_source(tmp_home / "pitch")
        with FakeAnkiConnect() as fake:
            fake.seed_deck(DECK)
            fake.seed_model(MODEL, MODEL_FIELDS)
            config = dataclasses.replace(
                AnkiMinerConfig(),
                ankiconnect_url=fake.url,
                anki_deck_name=DECK,
                anki_note_type=MODEL,
                anki_fields=dict(FIELD_MAP),
                card_type="word_and_sentence",
                # Deterministic + offline: no jpod101/gTTS reach, no sentence TTS.
                expression_audio_chain=(),
                reading_min_occurrence=1,
                use_known_words_db=False,
                # Widest possible watch surface: the default (True) cards one
                # word per subtitle line, so a drift that only moved a
                # deduped-away lemma would be invisible to the canary.
                deduplicate_sentences=False,
                # Pin every resource chain at the seeded fixture data.
                dictionary_chain=(ChainEntry(kind="indexed", dict_id=DEFAULT_DICT_ID, enabled=True),),
                frequency_chain=(FreqEntry(source_id=FREQ_SOURCE_ID, enabled=True),),
                pitch_chain=(PitchSourceEntry(source_id=PITCH_SOURCE_ID, enabled=True),),
            )
            processor = create_episode_processor(config, NullPresenter())
            try:
                processor.process_reading(_document())
            finally:
                processor.close()
            notes = [
                {
                    "deckName": n["deckName"],
                    "modelName": n["modelName"],
                    "tags": sorted(n["tags"]),
                    "fields": dict(n["fields"]),
                }
                for n in fake._notes.values()
            ]
    finally:
        restore_home_patches(saved)
    return sorted(notes, key=lambda n: json.dumps(n, ensure_ascii=False, sort_keys=True))


def test_ja_note_payloads_match_baseline(tmp_path: Path) -> None:
    """Every field of every ja-mined note is byte-identical to the baseline."""
    payloads = capture_payloads(tmp_path / "home")
    if os.environ.get("ANKI_MINER_CANARY_CAPTURE") == "1":
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(payloads, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("baseline captured; re-run without ANKI_MINER_CANARY_CAPTURE")
    assert BASELINE_PATH.exists(), (
        f"{BASELINE_PATH} missing — capture it with ANKI_MINER_CANARY_CAPTURE=1 "
        f".venv/bin/pytest tests/e2e/test_ja_drift_canary.py -m e2e -v"
    )
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert payloads == expected


def test_baseline_is_non_trivial_and_spans_every_mapped_field() -> None:
    """The baseline must have notes and must key every mapped field name."""
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert len(expected) >= 3
    mapped = set(FIELD_MAP.values())
    for note in expected:
        assert mapped <= set(note["fields"]), sorted(mapped - set(note["fields"]))
