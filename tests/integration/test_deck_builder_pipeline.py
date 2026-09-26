"""Integration tests for the full deck-builder pipeline.

Exercises the pre-pass → preview → (gated) build flow end to end with only
external I/O mocked: AnkiConnect (via ``anki_service.post_action`` and the
media store's calls), media extraction (via
``MediaExtractorService.extract_media_batch``), and definition lookups (via
``DefinitionService.get_definitions_batch`` / ``get_glossaries_batch``).

Real services used throughout: ``SubtitleParserService`` (with real
fugashi/MeCab), ``WordFilterService``, ``AnkiService``, ``EpisodeProcessor``,
``FilePairMatcher``, ``DeckBuilderWorker`` on Batch's season pipeline,
``aggregate``, ``rank_select`` and ``build_preview``.

Fixture corpus
--------------
Two .ass subtitle files for the same fictional show::

    ep01: 食べる (×2), 走る (×1)  →  3 tokens
    ep02: 食べる (×1), 本 (×1)    →  2 tokens

Combined: 食べる×3, 走る×1, 本×1 → 5 total tokens, 3 unique lemmas.

- ``食べる`` appears in BOTH episodes — the cross-episode dedup test.
- ``走る`` is unique to ep01.
- ``本`` is unique to ep02.
- ep01 has a repeated token so coverage math is non-trivial.

With ALL selection mode and no known words, the expected preview is:
    total_tokens=5, unique_lemmas=3, candidate_count=3,
    projected_coverage_pct=100.0, known_skipped=0, card_count=3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pysubs2
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
from anki_miner.models.deck_build import DeckBuildPreview, DeckBuildRequest, DeckCorpus, DeckSelectionMode
from anki_miner.models.media import MediaData
from anki_miner.models.word import TokenizedWord
from anki_miner.presenters import NullPresenter
from anki_miner.services.corpus_aggregator import build_preview, rank_select

_DUPLICATE_ERROR = "cannot create note because it is a duplicate"

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _write_ass(path: Path, events: list[tuple[float, float, str]]) -> None:
    """Write a minimal .ass subtitle file. events: [(start_sec, end_sec, text), ...]."""
    subs = pysubs2.SSAFile()
    for start_sec, end_sec, text in events:
        subs.append(pysubs2.SSAEvent(start=int(start_sec * 1000), end=int(end_sec * 1000), text=text))
    subs.save(str(path))


class _FakeAnki:
    """A stateful AnkiConnect stand-in, dispatching ``post_action`` by action name.

    - ``modelNames`` / ``modelFieldNames`` → the configured note type and fields.
    - ``createDeck`` → records the deck as existing (and the call).
    - ``deckNames`` → "Default" plus every deck createDeck was asked for.
    - ``findNotes`` / ``notesInfo`` → the known words, as notes.
    - ``canAddNotesWithErrorDetail`` → a duplicate error for a note whose first
      field is already in its deck, else addable.
    - ``addNotes`` → stores the notes in their decks, returns sequential IDs.
    - anything else → None (safe default).

    Stateful on purpose. Pre-flight VERIFIES the deck instead of creating it,
    and Deck Builder mines into request.deck_name — NOT the caller's
    config.anki_deck_name — so answering deckNames from what createDeck was
    asked for pins the ensure_deck-before-preflight ordering: move ensure_deck
    after the run starts and deckNames no longer contains the deck, so the
    pipeline goes red. One instance can serve two builds, which is how the
    rebuild test sees the first build's notes.
    """

    def __init__(self, config: AnkiMinerConfig, known_words: set[str] | None = None) -> None:
        self.config = config
        self.known_words = sorted(known_words or ())
        self.create_deck_calls: list[str] = []
        self.add_notes_calls: list[dict] = []
        self.probe_calls: list[list[dict]] = []
        self.deck_fronts: dict[str, set[str]] = {}
        self._next_note_id = 1000

    def post_action(self, url: str, action: str, params: dict | None = None, timeout: int = 30) -> Any:
        params = params or {}
        if action == "modelNames":
            return [self.config.anki_note_type]
        if action == "modelFieldNames":
            return list(self.config.anki_fields.values())
        if action == "createDeck":
            deck = params.get("deck", "")
            self.create_deck_calls.append(deck)
            self.deck_fronts.setdefault(deck, set())
            return 1234
        if action == "deckNames":
            return ["Default", *sorted(self.deck_fronts)]
        if action == "findNotes":
            return list(range(1, len(self.known_words) + 1))
        if action == "notesInfo":
            requested = len(params.get("notes", []))
            return [{"fields": {"word": {"value": w}}} for w in self.known_words[:requested]]
        if action == "canAddNotesWithErrorDetail":
            notes = params.get("notes", [])
            self.probe_calls.append(notes)
            return [
                (
                    {"canAdd": False, "error": _DUPLICATE_ERROR}
                    if self._front(note) in self.deck_fronts.get(note["deckName"], set())
                    else {"canAdd": True, "error": None}
                )
                for note in notes
            ]
        if action == "addNotes":
            notes = params.get("notes", [])
            self.add_notes_calls.append(params)
            for note in notes:
                self.deck_fronts.setdefault(note["deckName"], set()).add(self._front(note))
            start = self._next_note_id
            self._next_note_id += len(notes)
            return list(range(start, start + len(notes)))
        return None

    @staticmethod
    def _front(note: dict) -> str:
        return str(next(iter(note["fields"].values())))

    def mined_words(self, calls: list[dict] | None = None) -> list[str]:
        calls = self.add_notes_calls if calls is None else calls
        return [note["fields"]["word"] for params in calls for note in params["notes"]]


def _fake_extract_media_batch(tmp_path: Path) -> Any:
    """Return an ``extract_media_batch`` side_effect that creates stub files.

    For each word, writes a small fake JPEG so ``MediaData.has_screenshot``
    returns True, which is the gate that lets words through to card creation.
    The stub file contains only a few bytes — its contents are not inspected
    during card creation (the storeMediaFile call is to AnkiConnect, which we mock).
    """
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _side_effect(
        video_file: Path, words: list[TokenizedWord], *args: Any, **kwargs: Any
    ) -> list[tuple[TokenizedWord, MediaData]]:
        results = []
        for word in words:
            safe = word.lemma.replace("/", "_")
            ss = screenshot_dir / f"{safe}.jpg"
            ss.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
            results.append(
                (
                    word,
                    MediaData(
                        screenshot_path=ss,
                        audio_path=None,
                        screenshot_filename=ss.name,
                        audio_filename=None,
                    ),
                )
            )
        return results

    return _side_effect


@pytest.fixture
def base_config(tmp_path):
    """Minimal config with all optional lookups disabled."""
    return AnkiMinerConfig(
        anki_deck_name="original_deck",
        anki_note_type="test_type",
        anki_fields={
            "word": "word",
            "sentence": "sentence",
            "definition": "definition",
            "picture": "picture",
            "audio": "audio",
            "expression_furigana": "expression_furigana",
            "sentence_furigana": "sentence_furigana",
        },
        media_temp_folder=tmp_path / "media",
        jmdict_path=tmp_path / "JMdict_e",
        max_parallel_workers=1,
        use_blacklist=False,
        use_whitelist=False,
        use_known_words_db=False,
        include_known_words=False,
        # Keep the build off the real ~/.anki_miner — in particular the known
        # words DB, which would otherwise poison skip-known tests.
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
    )


@pytest.fixture
def show_folders(tmp_path) -> tuple[Path, Path]:
    """A video folder and a subtitle folder forming a 2-episode show.

    ep01: 食べる (×2), 走る (×1)
    ep02: 食べる (×1), 本 (×1)
    """
    video_dir = tmp_path / "video"
    subs_dir = tmp_path / "subs"
    video_dir.mkdir()
    subs_dir.mkdir()

    _write_ass(subs_dir / "ep01.ass", [(1.0, 3.0, "食べる"), (4.0, 6.0, "走る"), (7.0, 9.0, "食べる")])
    _write_ass(subs_dir / "ep02.ass", [(1.0, 3.0, "食べる"), (4.0, 6.0, "本")])

    # Video files must exist on disk (FilePairMatcher lists them, and
    # EpisodeProcessor opens a temp dir based on the video stem); their
    # contents are not read — media extraction is mocked.
    (video_dir / "ep01.mkv").touch()
    (video_dir / "ep02.mkv").touch()

    return video_dir, subs_dir


def _run_build(
    base_config: AnkiMinerConfig,
    show_folders: tuple[Path, Path],
    tmp_path: Path,
    anki: _FakeAnki,
    *,
    skip_known: bool,
    deck_name: str = "My Anime Deck",
) -> tuple[list[DeckCorpus], list[int]]:
    """Run a pre-confirmed ALL-mode ``DeckBuilderWorker`` synchronously.

    Returns ``(corpora, run_card_totals)``: every ``preview_ready`` payload and
    every ``queue_finished`` card total.
    """
    video_dir, subs_dir = show_folders
    request = DeckBuildRequest(
        video_folder=video_dir,
        subtitle_folder=subs_dir,
        deck_name=deck_name,
        skip_known=skip_known,
        review=False,
    )
    worker = DeckBuilderWorker(request, base_config, NullPresenter())

    corpora: list[DeckCorpus] = []
    totals: list[int] = []
    errors: list[str] = []
    worker.preview_ready.connect(corpora.append)
    worker.queue_finished.connect(lambda cards, _coverage: totals.append(cards))
    worker.error.connect(errors.append)
    worker.item_failed.connect(lambda _id, message, _cards: errors.append(message))
    # Pre-confirmed: the gate passes straight through, so run() stays synchronous.
    worker.confirm(DeckSelectionMode.ALL, 0.0)

    with (
        patch("anki_miner.services.anki_service.post_action", side_effect=anki.post_action),
        # Media uploads route through anki_media_store; stub its multi POST
        # with one non-error sub-result per action so every file counts as
        # stored without touching the network.
        patch(
            "anki_miner.services.anki_media_store.post_multi",
            side_effect=lambda url, actions, timeout=30: [None] * len(actions),
        ),
        patch("anki_miner.services.anki_media_store.post_action", side_effect=anki.post_action),
        patch(
            "anki_miner.services.media_extractor.MediaExtractorService.extract_media_batch",
            side_effect=_fake_extract_media_batch(tmp_path),
        ),
        patch(
            "anki_miner.services.definition_service.DefinitionService.get_definitions_batch",
            side_effect=lambda lemmas, *a, **kw: ["test definition"] * len(lemmas),
        ),
        patch(
            "anki_miner.services.definition_service.DefinitionService.get_glossaries_batch",
            side_effect=lambda lemmas, *a, **kw: [None] * len(lemmas),
        ),
    ):
        worker.run()

    assert not errors, f"Worker raised errors: {errors}"
    return corpora, totals


# --------------------------------------------------------------------------- #
# Test 1: preview numbers
# --------------------------------------------------------------------------- #


class TestDeckBuilderPreview:
    def test_all_mode_preview_numbers(self, qapp, base_config, show_folders, tmp_path):
        """The pre-pass emits a DeckCorpus whose preview matches the 2-ep fixture."""
        corpora, _ = _run_build(base_config, show_folders, tmp_path, _FakeAnki(base_config), skip_known=False)

        assert len(corpora) == 1
        corpus = corpora[0]
        # Real MeCab counts: 食べる×3 + 走る×1 + 本×1.
        assert dict(corpus.counts) == {"食べる": 3, "走る": 1, "本": 1}
        assert corpus.episodes == 2
        preview = build_preview(corpus, rank_select(corpus.counts, DeckSelectionMode.ALL, 0.0))
        assert preview == DeckBuildPreview(
            total_tokens=5,
            unique_lemmas=3,
            candidate_count=3,
            projected_coverage_pct=pytest.approx(100.0),
            known_skipped=0,
            card_count=3,
        )


# --------------------------------------------------------------------------- #
# Test 2: deck routing + createDeck called exactly once
# --------------------------------------------------------------------------- #


class TestDeckBuilderRouting:
    def test_createdeck_called_exactly_once(self, qapp, base_config, show_folders, tmp_path):
        """ensure_deck fires createDeck once, with the target deck name."""
        anki = _FakeAnki(base_config)

        _run_build(base_config, show_folders, tmp_path, anki, skip_known=False, deck_name="My Anime Deck")

        assert anki.create_deck_calls == ["My Anime Deck"]

    def test_addnotes_routed_to_named_deck(self, qapp, base_config, show_folders, tmp_path):
        """Every note in addNotes payloads must carry the target deck name."""
        deck_name = "My Anime Deck"
        anki = _FakeAnki(base_config)

        _run_build(base_config, show_folders, tmp_path, anki, skip_known=False, deck_name=deck_name)

        assert anki.add_notes_calls, "Expected at least one addNotes call"
        for call_params in anki.add_notes_calls:
            notes = call_params.get("notes", [])
            assert notes, "addNotes called with empty notes list"
            for note in notes:
                assert (
                    note["deckName"] == deck_name
                ), f"Note routed to wrong deck: got {note['deckName']!r}, expected {deck_name!r}"


# --------------------------------------------------------------------------- #
# Test 3: cross-episode dedup — 食べる carded once, not twice
# --------------------------------------------------------------------------- #


class TestCrossEpisodeDedup:
    def test_shared_lemma_carded_once(self, qapp, base_config, show_folders, tmp_path):
        """食べる appears in both episodes but must produce exactly one card total."""
        anki = _FakeAnki(base_config)

        _, totals = _run_build(base_config, show_folders, tmp_path, anki, skip_known=False)

        mined_words = anki.mined_words()
        assert (
            mined_words.count("食べる") == 1
        ), f"食べる should be carded exactly once (cross-episode dedup), got: {mined_words}"
        # Total: 食べる + 走る + 本 = 3 distinct cards.
        assert sorted(mined_words) == sorted(["食べる", "走る", "本"])
        # queue_finished sums cards_created across both episodes.
        assert totals == [3]


# --------------------------------------------------------------------------- #
# Test 4: skip_known OFF vs ON
# --------------------------------------------------------------------------- #


class TestSkipKnownToggle:
    def test_skip_known_off_cards_everything(self, qapp, base_config, show_folders, tmp_path):
        """skip_known=False: all 3 lemmas are carded even when 走る is 'known'."""
        # 走る is in the collection, but skip_known OFF must ignore that.
        anki = _FakeAnki(base_config, known_words={"走る"})

        _run_build(base_config, show_folders, tmp_path, anki, skip_known=False)

        mined_words = anki.mined_words()
        assert (
            len(mined_words) == 3
        ), f"skip_known OFF: expected 3 cards (known words ignored), got {len(mined_words)}: {mined_words}"

    def test_skip_known_on_skips_known_lemma(self, qapp, base_config, show_folders, tmp_path):
        """skip_known=True: a lemma already in the collection is not re-carded.

        走る is returned by the mocked findNotes/notesInfo pair as a known word.
        With skip_known ON, the pipeline calls get_existing_vocabulary() and
        subtracts it. The result must be 2 cards (食べる + 本), not 3.
        """
        anki = _FakeAnki(base_config, known_words={"走る"})

        _run_build(base_config, show_folders, tmp_path, anki, skip_known=True)

        mined_words = anki.mined_words()
        assert "走る" not in mined_words, f"skip_known ON: 走る is known, must not be carded; got: {mined_words}"
        assert len(mined_words) == 2, f"skip_known ON: expected 2 cards (走る known), got {len(mined_words)}"

    def test_skip_known_off_produces_more_cards_than_skip_known_on(self, qapp, base_config, show_folders, tmp_path):
        """skip_known OFF produces strictly more cards than ON when a known word exists."""
        off = _FakeAnki(base_config, known_words={"走る"})
        on = _FakeAnki(base_config, known_words={"走る"})

        _run_build(base_config, show_folders, tmp_path, off, skip_known=False)
        _run_build(base_config, show_folders, tmp_path, on, skip_known=True)

        total_off, total_on = len(off.mined_words()), len(on.mined_words())
        assert total_off > total_on, f"skip_known OFF should produce more cards than ON: {total_off} vs {total_on}"


# --------------------------------------------------------------------------- #
# Test 5: rebuilding into a deck that already holds the cards
# --------------------------------------------------------------------------- #


class TestRebuild:
    def test_rebuild_with_skip_known_off_adds_nothing(self, qapp, base_config, show_folders, tmp_path):
        """A second build into the same deck is caught by the deck-scoped duplicate probe."""
        anki = _FakeAnki(base_config)

        _, first_totals = _run_build(base_config, show_folders, tmp_path, anki, skip_known=False)
        first_adds = len(anki.add_notes_calls)
        first_probes = len(anki.probe_calls)
        _, second_totals = _run_build(base_config, show_folders, tmp_path, anki, skip_known=False)

        assert first_totals == [3]
        # createDeck is idempotent and asked once per build.
        assert anki.create_deck_calls == ["My Anime Deck", "My Anime Deck"]
        second_probe = [note for notes in anki.probe_calls[first_probes:] for note in notes]
        assert sorted(_FakeAnki._front(note) for note in second_probe) == sorted(["食べる", "走る", "本"])
        assert all(note["options"]["duplicateScope"] == "deck" for note in second_probe)
        assert anki.mined_words(anki.add_notes_calls[first_adds:]) == []
        assert second_totals == [0]
