"""AnkiConnect request order of card creation (services-07).

``create_cards_batch`` runs against the e2e harness's in-memory
:class:`FakeAnkiConnect`, answered in-process through the ``requests.post``
seam that ``services/_ankiconnect`` documents, so no socket opens. Requests and
progress callbacks land in one event log, so their relative order is pinned too.

Pinned elsewhere and not repeated here: Stop between confirmed batches
(``test_note_submission_invariants.py``), a failing batch after a confirmed one
(``test_anki_service.py::TestAnkiWriteState``) and ``add_notes_raw``
(``test_anki_service.py::TestAddNotesRaw``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import AnkiWriteState, CardPayload, MediaData, TokenizedWord
from anki_miner.services import _ankiconnect
from anki_miner.services.anki_service import AnkiService
from tests.e2e.fake_ankiconnect import FakeAnkiConnect

_MODEL = "Lapis"


class _Response:
    """The slice of ``requests.Response`` that ``post_action``/``post_multi`` read."""

    def __init__(self, body: dict) -> None:
        self._body = body
        self.content = json.dumps(body).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Anki:
    """The fake collection plus one ordered log of requests and progress callbacks.

    A request logs ``(action, size, timeout)``: size is the note count, the
    ``multi`` sub-action count, or the ``findNotes`` query.
    """

    def __init__(self, fake: FakeAnkiConnect) -> None:
        self.fake = fake
        self.events: list[tuple] = []

    def post(self, url: str, json: dict, timeout: float, **_: object) -> _Response:
        params = json["params"]
        if "notes" in params:
            size = len(params["notes"])
        elif "actions" in params:
            size = len(params["actions"])
        else:
            size = params.get("query")
        self.events.append((json["action"], size, timeout))
        return _Response(self.fake._envelope(json))

    def on_start(self, total: int, description: str) -> None:
        self.events.append(("on_start", total))

    def on_progress(self, current: int, item_description: str) -> None:
        self.events.append(("on_progress", current))

    def on_complete(self) -> None:
        self.events.append(("on_complete",))


@pytest.fixture
def anki(monkeypatch):
    def _install(*seed: tuple[str, str]) -> _Anki:
        fake = FakeAnkiConnect()
        for deck, word in seed:
            fake._envelope(
                {
                    "action": "addNotes",
                    "params": {"notes": [{"deckName": deck, "modelName": _MODEL, "fields": {"Expression": word}}]},
                }
            )
        recorder = _Anki(fake)
        monkeypatch.setattr(_ankiconnect.requests, "post", recorder.post)
        return recorder

    return _install


def _config(tmp_path: Path, **overrides: object) -> AnkiMinerConfig:
    fields = {"word": "Expression", "sentence": "Sentence", "definition": "MainDefinition", "picture": "Picture"}
    fields |= {"audio": "SentenceAudio", "expression_furigana": "Furigana", "sentence_furigana": "SentenceFurigana"}
    return AnkiMinerConfig(
        anki_deck_name="Mining",
        anki_note_type=_MODEL,
        anki_fields=fields,
        media_temp_folder=tmp_path / "media",
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
        **overrides,
    )


def _payload(tmp_path: Path, i: int, word: str | None = None, *, media: bool = False) -> CardPayload:
    text = word or f"語{i:03d}"
    token = TokenizedWord(
        surface=text, lemma=text, reading="ゴ", sentence=f"{text}の文。", start_time=i, end_time=i + 1, duration=1.0
    )
    files = MediaData()
    if media:
        shot, clip = tmp_path / f"shot_{i}.jpg", tmp_path / f"clip_{i}.mp3"
        shot.write_bytes(f"jpeg-{i}".encode())
        clip.write_bytes(f"mp3-{i}".encode())
        files = MediaData(
            screenshot_path=shot, audio_path=clip, screenshot_filename=shot.name, audio_filename=clip.name
        )
    return CardPayload(word=token, media=files, definition=f"<div>{text}</div>")


def test_each_chunk_is_probed_then_uploaded_then_submitted(tmp_path, anki):
    """100/100/5 chunks. 語003 is already in Anki; 語050 recurs in chunk 2 after
    chunk 1 created it; 語201 twice in chunk 3 passes the probe and loses one
    addNotes slot. Duplicates never reach the upload."""
    recorder = anki(("Mining", "語003"))
    service = AnkiService(_config(tmp_path))
    payloads = [_payload(tmp_path, i, media=i in (0, 1, 100, 150, 202)) for i in range(205)]
    payloads[150] = _payload(tmp_path, 150, "語050", media=True)
    payloads[202] = _payload(tmp_path, 202, "語201", media=True)

    ids = service.create_cards_batch(payloads, recorder)

    assert recorder.events == [
        ("on_start", 205),
        ("canAddNotesWithErrorDetail", 100, 60),
        ("multi", 4, 30),
        ("addNotes", 99, 60),
        ("on_progress", 101),
        ("canAddNotesWithErrorDetail", 100, 60),
        ("multi", 2, 30),
        ("addNotes", 99, 60),
        ("on_progress", 202),
        ("canAddNotesWithErrorDetail", 5, 60),
        ("multi", 2, 30),
        ("addNotes", 5, 60),
        ("on_progress", 205),
        ("on_complete",),
    ]
    assert ids == list(range(1001, 1203))
    assert service.last_skipped_duplicates == 3
    assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED


def test_excluded_deck_admission_scans_after_on_start_then_submits_once(tmp_path, anki):
    """語001 exists only in the excluded deck, so the admission scan drops it;
    語000 repeated in the run is admitted once."""
    recorder = anki(("Archive", "語001"))
    service = AnkiService(_config(tmp_path, excluded_decks=("Archive",)))
    payloads = [_payload(tmp_path, 0, media=True), _payload(tmp_path, 1), _payload(tmp_path, 2)]
    payloads.append(_payload(tmp_path, 3, "語000"))

    ids = service.create_cards_batch(payloads, recorder)

    assert recorder.events == [
        ("on_start", 4),
        ("findNotes", 'deck:* -deck:"Archive"', 30),
        ("notesInfo", 1, 30),
        ("canAddNotesWithErrorDetail", 2, 60),
        ("multi", 2, 30),
        ("addNotes", 2, 60),
        ("on_progress", 4),
        ("on_complete",),
    ]
    assert ids == [1001, 1002]
    assert service.last_created_mined_forms == ["語000", "語002"]
    assert service.last_skipped_duplicates == 2


def test_empty_input_sends_nothing_and_resets_the_receipts(tmp_path, anki):
    recorder = anki()
    service = AnkiService(_config(tmp_path))
    service.last_created_note_ids = [9]
    service.last_created_mined_forms = ["stale"]
    service.last_created_lemmas = ["stale"]
    service.last_skipped_duplicates = 4
    service.last_media_store_failures = 2

    assert service.create_cards_batch([], recorder) == []

    assert recorder.events == []
    assert (service.last_created_note_ids, service.last_created_mined_forms, service.last_created_lemmas) == (
        [],
        [],
        [],
    )
    assert (service.last_skipped_duplicates, service.last_media_store_failures) == (0, 0)
    assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE
