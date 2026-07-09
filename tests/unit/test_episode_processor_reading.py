"""Tests for EpisodeProcessor.process_reading (reading-tab orchestration).

Mirrors the process_episode phase skeleton over ReadingDocuments: text-unit
parse (phase 1') → filter → image materialization + expression audio (phase 3')
→ definitions (phase 4) → cards (phase 5). External services are mocked; the
WordFilterService is real so filter_unknown / filter_by_episode_count /
attach_occurrence_counts run their production logic.
"""

from __future__ import annotations

import collections
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import UnidentifiedImageError

from anki_miner.exceptions import AnkiMinerException, SetupError
from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor, _format_timestamp
from anki_miner.presenters import NullPresenter
from anki_miner.services.reading.models import ImageRef, ReadingDocument, ReadingUnit
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService

_IMG = "anki_miner.orchestration.episode_processor.prepare_card_image"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _word(lemma: str, index: int, *, pos: str = "名詞", surface: str | None = None) -> TokenizedWord:
    """A tokenized word whose dummy start_time is the unit index (parse contract)."""
    surface = surface if surface is not None else lemma
    return TokenizedWord(
        surface=surface,
        lemma=lemma,
        reading="カナ",
        sentence=f"{surface}の文",
        start_time=float(index),
        end_time=float(index),
        duration=0.0,
        pos=pos,
    )


def _unit(index: int, *, label: str | None = None, image_ref: ImageRef | None = None) -> ReadingUnit:
    return ReadingUnit(
        text=f"unit{index}",
        index=index,
        location_label=label if label is not None else f"p.{index}",
        image_ref=image_ref,
    )


def _document(
    units: list[ReadingUnit],
    *,
    kind: str = "manga",
    series: str = "My Manga",
    episode: str = "vol01",
    title: str = "My Manga",
    warnings: list[str] | None = None,
) -> ReadingDocument:
    return ReadingDocument(
        title=title,
        kind=kind,  # type: ignore[arg-type]
        series=series,
        episode=episode,
        units=units,
        warnings=warnings if warnings is not None else [],
    )


def _make_anki_service() -> MagicMock:
    svc = MagicMock(name="AnkiService")
    svc.get_existing_vocabulary.return_value = set()
    svc.last_created_note_ids = []
    svc.last_media_store_failures = 0
    svc.last_skipped_duplicates = 0

    def _create(card_data, pc=None):
        if pc is not None:
            pc.on_start(len(card_data), "cards")
            for i in range(len(card_data)):
                pc.on_progress(i + 1, "c")
            pc.on_complete()
        svc.last_created_note_ids = list(range(1, len(card_data) + 1))
        svc.last_card_data = list(card_data)
        return len(card_data)

    svc.create_cards_batch.side_effect = _create
    svc.last_card_data = []
    return svc


def _make_processor(
    config,
    *,
    subtitle_parser=None,
    anki_service=None,
    definition_service=None,
    presenter=None,
    stats_service=None,
    expression_audio_fetcher=None,
    sentence_audio_fetcher=None,
    word_list_service=None,
) -> EpisodeProcessor:
    subtitle_parser = subtitle_parser or MagicMock(name="SubtitleParser")
    definition_service = definition_service or MagicMock(name="DefinitionService")
    definition_service.has_offline_definitions.side_effect = lambda lemmas: dict.fromkeys(lemmas, True)

    def _defs(pairs, pc=None, fb=None):
        if pc is not None:
            pc.on_start(len(pairs), "definitions")
            for i in range(len(pairs)):
                pc.on_progress(i + 1, "d")
            pc.on_complete()
        return ["<def>"] * len(pairs)

    def _gloss(pairs, pc=None):
        if pc is not None:
            pc.on_start(len(pairs), "glossaries")
            for i in range(len(pairs)):
                pc.on_progress(i + 1, "g")
            pc.on_complete()
        return ["<gloss>"] * len(pairs)

    definition_service.get_definitions_batch.side_effect = _defs
    definition_service.get_glossaries_batch.side_effect = _gloss

    return EpisodeProcessor(
        config=config,
        subtitle_parser=subtitle_parser,
        word_filter=WordFilterService(config),
        media_extractor=MagicMock(name="MediaExtractor"),
        definition_service=definition_service,
        anki_service=anki_service or _make_anki_service(),
        presenter=presenter or NullPresenter(),
        stats_service=stats_service,
        expression_audio_fetcher=expression_audio_fetcher,
        sentence_audio_fetcher=sentence_audio_fetcher,
        word_list_service=word_list_service,
    )


class _RecordingProgress:
    """Captures the global pct values (and stage/item labels) emitted through a StageWeightedProgress."""

    def __init__(self) -> None:
        self.pcts: list[int] = []
        self.start_descs: list[str] = []
        self.progress_descs: list[str] = []

    def on_start(self, total: int, description: str) -> None:  # noqa: D401
        self.start_descs.append(description)

    def on_progress(self, current: int, item_description: str) -> None:
        self.pcts.append(current)
        self.progress_descs.append(item_description)

    def on_complete(self) -> None:
        pass

    def on_error(self, item_description: str, error_message: str) -> None:
        pass


def _parse_returning(words, line_index, counts):
    """A parse_text_units side_effect that records the want_line_index arg."""
    calls: list[bool] = []

    def _parse(units, want_line_index):
        calls.append(want_line_index)
        return (list(words), line_index, counts)

    _parse.calls = calls  # type: ignore[attr-defined]
    return _parse


def _sources(anki_service) -> list[str]:
    return [p.extra_fields["source"] for p in anki_service.last_card_data if p.extra_fields]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_phases_in_order_cards_created(test_config):
    """1. Phases invoked in order; cards created end-to-end, no ffmpeg touched."""
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock(name="SubtitleParser")
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    manager = MagicMock()
    manager.attach_mock(sp.parse_text_units, "parse")
    manager.attach_mock(proc.definition_service.get_definitions_batch, "defs")
    manager.attach_mock(anki.create_cards_batch, "cards")

    doc = _document([_unit(0), _unit(1)])
    result = proc.process_reading(doc)

    assert result.cards_created == 2
    # media_extractor is video-only and must never run on the reading path.
    proc.media_extractor.extract_media_batch.assert_not_called()
    ordered = [name for name, _, _ in manager.mock_calls]
    assert ordered == ["parse", "defs", "cards"]


def test_d4_line_index_fused_for_iplus_one(test_config):
    """2. D4: i+1 on + curation None + not preview → line index built, cards created."""
    cfg = replace(test_config, use_i_plus_one_filter=True)
    words = [_word("犬", 0), _word("猫", 1)]
    # Each lemma is the sole unknown on its own line → both are i+1.
    from anki_miner.models import LineLemmas

    line_index = [
        LineLemmas(line_text="犬の文", lemmas=frozenset({"犬"}), start_time=0.0, end_time=0.0, duration=0.0),
        LineLemmas(line_text="猫の文", lemmas=frozenset({"猫"}), start_time=1.0, end_time=1.0, duration=0.0),
    ]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock(name="SubtitleParser")
    parse = _parse_returning(words, line_index, counts)
    sp.parse_text_units.side_effect = parse
    proc = _make_processor(cfg, subtitle_parser=sp)

    result = proc.process_reading(_document([_unit(0), _unit(1)]))

    assert parse.calls == [True]  # want_line_index fused True from i+1 alone
    assert result.cards_created > 0


def test_min_occurrence_filters_singletons(test_config):
    """3. reading_min_occurrence=2 drops hapax; default 1 keeps everything."""
    words = [_word("頻", 0), _word("稀", 1)]
    counts = collections.Counter({"頻": 3, "稀": 1})

    # Default (=1): no filtering.
    sp1 = MagicMock()
    sp1.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki1 = _make_anki_service()
    _make_processor(test_config, subtitle_parser=sp1, anki_service=anki1).process_reading(
        _document([_unit(0), _unit(1)])
    )
    assert anki1.create_cards_batch.call_args.args[0].__len__() == 2

    # =2: only 頻 (3 occurrences) survives.
    cfg2 = replace(test_config, reading_min_occurrence=2)
    sp2 = MagicMock()
    sp2.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki2 = _make_anki_service()
    res = _make_processor(cfg2, subtitle_parser=sp2, anki_service=anki2).process_reading(
        _document([_unit(0), _unit(1)])
    )
    assert res.cards_created == 1
    fronts = {p.word.lemma for p in anki2.last_card_data}
    assert fronts == {"頻"}


def test_whitelist_force_includes_past_min_occurrence(test_config, tmp_path):
    """A whitelisted hapax survives reading_min_occurrence=2 (force-include bypasses
    the reading path's occurrence floor, which runs OUTSIDE _phase2_filter)."""
    wl = tmp_path / "wl.txt"
    wl.write_text("稀\n", encoding="utf-8")
    wls = WordListService(whitelist_path=wl)
    wls.load()

    cfg = replace(test_config, reading_min_occurrence=2, use_whitelist=True)
    words = [_word("頻", 0), _word("稀", 1)]  # 頻 appears 3×, 稀 is a hapax
    counts = collections.Counter({"頻": 3, "稀": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()

    _make_processor(cfg, subtitle_parser=sp, anki_service=anki, word_list_service=wls).process_reading(
        _document([_unit(0), _unit(1)])
    )

    # Both survive: 頻 by occurrence count, 稀 by whitelist force-include.
    fronts = {p.word.lemma for p in anki.last_card_data}
    assert fronts == {"頻", "稀"}


def test_no_mineable_words_message_names_filters_on_reading_path(test_config):
    """Regression B (reading path): reading_min_occurrence runs OUTSIDE _phase2_filter
    (episode_processor.py:1779), so it can empty the list after words survived the
    known-vocab filter. The shared terminal helper must then say 'removed by active
    filters', NOT 'All words already in Anki!' — proving the filter-agnostic wording
    is correct even when a non-_phase2 filter does the emptying."""
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})  # both hapax
    cfg = replace(test_config, reading_min_occurrence=2)  # drops every hapax → empty set

    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    presenter = MagicMock(spec=NullPresenter())
    proc = _make_processor(cfg, subtitle_parser=sp, presenter=presenter)

    proc.process_reading(_document([_unit(0), _unit(1)]))

    assert any(
        "removed by active filters" in str(c.args[0]).lower() for c in presenter.show_warning.call_args_list
    ), presenter.show_warning.call_args_list
    assert not any("already in anki" in str(c.args[0]).lower() for c in presenter.show_info.call_args_list)


def test_occurrence_counts_attached_for_curation(test_config):
    """4. attach_occurrence_counts effect visible to the curation callback."""
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 5, "猫": 2})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    proc = _make_processor(test_config, subtitle_parser=sp)

    seen = {}

    def curate(curated_words):
        for w in curated_words:
            seen[w.lemma] = w.occurrence_count
        return curated_words

    proc.process_reading(_document([_unit(0), _unit(1)]), curation_callback=curate)
    assert seen == {"犬": 5, "猫": 2}


def test_image_materialized_once_per_ref(test_config):
    """5. Shared page → one prepare_card_image call; each word gets the picture."""
    ref = ImageRef(Path("/pages/page01.png"))
    units = [_unit(0, image_ref=ref), _unit(1, image_ref=ref)]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_abc.jpg")
        proc.process_reading(_document(units))

    assert prep.call_count == 1
    pics = {p.media.screenshot_filename for p in anki.last_card_data}
    assert pics == {"reading_abc.jpg"}


def test_cover_fanout_book(test_config):
    """5b. Book cover shared by every unit → every word carries the cover."""
    cover = ImageRef(Path("/book.epub"), "cover.jpg")
    units = [_unit(0, image_ref=cover), _unit(1, image_ref=cover), _unit(2, image_ref=cover)]
    words = [_word("春", 0), _word("夏", 1), _word("秋", 2)]
    counts = collections.Counter({"春": 1, "夏": 1, "秋": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_cover.jpg")
        proc.process_reading(_document(units, kind="book", episode="Novel", title="Novel"))

    assert prep.call_count == 1
    assert all(p.media.screenshot_filename == "reading_cover.jpg" for p in anki.last_card_data)
    assert len(anki.last_card_data) == 3


def test_unmatched_page_no_picture(test_config):
    """6. Word with image_ref=None → no picture, run completes."""
    units = [_unit(0, image_ref=None)]
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    with patch(_IMG) as prep:
        res = proc.process_reading(_document(units))

    prep.assert_not_called()
    assert res.cards_created == 1
    assert anki.last_card_data[0].media.screenshot_filename is None


def test_unsafe_archive_warns_once_imageless(test_config):
    """7. Unsafe archive with 2 refs → one warning, no pictures, run completes."""
    archive = Path("/vol.cbz")
    units = [
        _unit(0, image_ref=ImageRef(archive, "p1.jpg")),
        _unit(1, image_ref=ImageRef(archive, "p2.jpg")),
    ]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    presenter = MagicMock(name="Presenter")
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki, presenter=presenter)

    with patch(_IMG, side_effect=SetupError("unsafe zip")):
        res = proc.process_reading(_document(units))

    assert presenter.show_warning.call_count == 1
    assert res.cards_created == 2
    assert all(p.media.screenshot_filename is None for p in anki.last_card_data)


def test_undecodable_page_warns_once_imageless(test_config):
    """7b. An undecodable page (PIL UnidentifiedImageError) never aborts the
    volume: one warning, that word imageless, other pages still materialize."""
    bad = ImageRef(Path("/pages/bad.png"))
    good = ImageRef(Path("/pages/good.png"))
    units = [_unit(0, image_ref=bad), _unit(1, image_ref=good)]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    presenter = MagicMock(name="Presenter")
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki, presenter=presenter)

    def _prep(ref, dest):
        if ref == bad:
            raise UnidentifiedImageError("boom")
        return Path("/tmp/reading_good.jpg")

    with patch(_IMG, side_effect=_prep):
        res = proc.process_reading(_document(units))

    assert presenter.show_warning.call_count == 1
    assert res.cards_created == 2
    by_lemma = {p.word.lemma: p.media.screenshot_filename for p in anki.last_card_data}
    assert by_lemma["犬"] is None  # undecodable page → no picture
    assert by_lemma["猫"] == "reading_good.jpg"  # the good page still materialized


def test_corrupt_archive_warns_once_imageless(test_config):
    """7c. A corrupt archive (BadZipFile — NOT an OSError subclass) shared by 2
    refs → exactly one warning, all its words imageless, run completes."""
    archive = Path("/vol.cbz")
    units = [
        _unit(0, image_ref=ImageRef(archive, "p1.jpg")),
        _unit(1, image_ref=ImageRef(archive, "p2.jpg")),
    ]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    presenter = MagicMock(name="Presenter")
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki, presenter=presenter)

    with patch(_IMG, side_effect=zipfile.BadZipFile("corrupt")) as prep:
        res = proc.process_reading(_document(units))

    assert prep.call_count == 1  # second ref short-circuits on failed_archives
    assert presenter.show_warning.call_count == 1
    assert res.cards_created == 2
    assert all(p.media.screenshot_filename is None for p in anki.last_card_data)


def test_unit_labels_hit_and_miss(test_config):
    """8. unit_labels hit → '@ p.42'; miss (synthetic start_time) → timestamp fallback."""
    units = [_unit(0, label="p.42")]  # only index 0 has a label
    words = [_word("犬", 0), _word("猫", 999)]  # 猫 has no matching unit
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    proc.process_reading(_document(units))

    by_lemma = {p.word.lemma: p.extra_fields["source"] for p in anki.last_card_data}
    assert by_lemma["犬"] == "My Manga — vol01 @ p.42"
    assert by_lemma["猫"] == f"My Manga — vol01 @ {_format_timestamp(999.0)}"


def test_expression_audio_after_images(test_config):
    """9. Expression audio fetched after images, band consumed when active."""
    cfg = replace(test_config, anki_fields={**dict(test_config.anki_fields), "expression_audio": "ExprAudio"})
    units = [_unit(0, image_ref=ImageRef(Path("/p0.png"))), _unit(1, image_ref=ImageRef(Path("/p1.png")))]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    order: list[str] = []
    fetcher = MagicMock(name="AudioFetcher")
    fetcher.stats.return_value = {}
    fetcher.fetch_candidates.side_effect = lambda cands, cancelled_check=None: order.append("fetch") or None
    proc = _make_processor(cfg, subtitle_parser=sp, expression_audio_fetcher=fetcher)
    assert proc._expression_audio_active is True

    def _prep(ref, dest):
        order.append("prep")
        return Path("/tmp/reading_x.jpg")

    rec = _RecordingProgress()
    with patch(_IMG, side_effect=_prep):
        proc.process_reading(_document(units), progress_callback=rec)

    assert fetcher.fetch_candidates.call_count == 2  # once per word
    assert order == ["prep", "prep", "fetch", "fetch"]  # every image before any audio
    assert rec.pcts == sorted(rec.pcts) and rec.pcts[-1] == 100


def test_note_ids_reset_at_run_start(test_config):
    """10. Stale note IDs from a prior run don't leak into a mid-run failure."""
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    anki.last_created_note_ids = [999]  # stale from a previous run
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)
    # Fail in phase 4, before any card is created (override the helper default).
    proc.definition_service.get_definitions_batch.side_effect = AnkiMinerException("phase4 boom")

    with patch(_IMG):
        res = proc.process_reading(_document([_unit(0)]))

    assert res.card_ids == []  # not [999]
    assert res.cards_created == 0


@pytest.mark.parametrize("glossary", [True, False])
def test_progress_bands_monotonic_reaches_100(test_config, glossary):
    """11. Emitted pct sequence monotonic, reaches 100, every band consumed."""
    fields = dict(test_config.anki_fields)
    if glossary:
        fields["glossary"] = "Glossary"
    cfg = replace(test_config, anki_fields=fields)
    words = [_word("犬", 0), _word("猫", 1), _word("鳥", 2)]
    counts = collections.Counter({"犬": 1, "猫": 1, "鳥": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    proc = _make_processor(cfg, subtitle_parser=sp)

    rec = _RecordingProgress()
    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_p.jpg")
        proc.process_reading(_document([_unit(0), _unit(1), _unit(2)]), progress_callback=rec)

    assert rec.pcts == sorted(rec.pcts)  # monotonic non-decreasing
    assert rec.pcts[-1] == 100
    assert all(0 <= p <= 100 for p in rec.pcts)
    # bands: image + defs + [gloss] + cards, each ticking once per word, plus
    # one label-refresh on_progress at each later-stage boundary (bands - 1),
    # plus finish.
    bands = 4 if glossary else 3
    assert len(rec.pcts) == bands * len(words) + (bands - 1) + 1


def test_zero_image_document_consumes_image_band(test_config):
    """11b. Text-only volume (no image refs) still consumes the 0.40 image band."""
    units = [_unit(0, image_ref=None), _unit(1, image_ref=None)]
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    proc = _make_processor(test_config, subtitle_parser=sp)

    rec = _RecordingProgress()
    with patch(_IMG) as prep:
        proc.process_reading(_document(units), progress_callback=rec)

    prep.assert_not_called()
    assert rec.pcts == sorted(rec.pcts) and rec.pcts[-1] == 100
    # image (2) + defs (2) + cards (2) + 2 boundary label-refreshes + finish (1)
    assert len(rec.pcts) == 3 * len(words) + (3 - 1) + 1


def test_book_image_stage_strings(test_config):
    """T2. Book document → 'card images' wording at all three image-stage sites."""
    units = [_unit(0, image_ref=ImageRef(Path("/book.epub"), "cover.jpg"))]
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    presenter = MagicMock(name="Presenter")
    proc = _make_processor(test_config, subtitle_parser=sp, presenter=presenter)

    rec = _RecordingProgress()
    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_cover.jpg")
        proc.process_reading(
            _document(units, kind="book", episode="Novel", title="Novel"),
            progress_callback=rec,
        )

    infos = [c.args[0] for c in presenter.show_info.call_args_list]
    assert "Step 3/5 — Preparing card images" in infos  # step banner
    assert rec.start_descs[0] == "Preparing card images"  # on_start desc
    assert f"Card image: {words[0].mined_form}" in rec.progress_descs  # per-word
    # No manga wording leaks into a book run.
    assert "Step 3/5 — Preparing page images" not in infos
    assert "Preparing page images" not in rec.start_descs
    assert "Page image: 犬" not in rec.progress_descs


def test_manga_image_stage_strings_unchanged(test_config):
    """T2. Manga document keeps 'page images' wording (regression guard)."""
    units = [_unit(0, image_ref=ImageRef(Path("/vol.cbz"), "p1.jpg"))]
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    presenter = MagicMock(name="Presenter")
    proc = _make_processor(test_config, subtitle_parser=sp, presenter=presenter)

    rec = _RecordingProgress()
    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_p.jpg")
        proc.process_reading(_document(units, kind="manga"), progress_callback=rec)

    infos = [c.args[0] for c in presenter.show_info.call_args_list]
    assert "Step 3/5 — Preparing page images" in infos  # step banner
    assert rec.start_descs[0] == "Preparing page images"  # on_start desc
    assert f"Page image: {words[0].mined_form}" in rec.progress_descs  # per-word
    assert "Card image: 犬" not in rec.progress_descs


def test_book_progress_bands_match_manga(test_config):
    """T2. Book run emits the same band structure as manga — change is label-only."""
    words = [_word("犬", 0), _word("猫", 1), _word("鳥", 2)]
    counts = collections.Counter({"犬": 1, "猫": 1, "鳥": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    proc = _make_processor(test_config, subtitle_parser=sp)

    rec = _RecordingProgress()
    with patch(_IMG) as prep:
        prep.return_value = Path("/tmp/reading_p.jpg")
        proc.process_reading(
            _document([_unit(0), _unit(1), _unit(2)], kind="book", episode="Novel", title="Novel"),
            progress_callback=rec,
        )

    assert rec.pcts == sorted(rec.pcts)  # monotonic non-decreasing
    assert rec.pcts[-1] == 100
    # Identical band math to manga (test 11): image + defs + cards, one tick per
    # word, plus a boundary label-refresh per later stage, plus finish.
    bands = 3
    assert len(rec.pcts) == bands * len(words) + (bands - 1) + 1


def test_warnings_emitted_before_phase1(test_config):
    """12. Each document.warnings entry surfaced via presenter.show_warning up front."""
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    order: list[str] = []
    sp = MagicMock()

    def _parse(units, want_line_index):
        order.append("parse")
        return (list(words), None, counts)

    sp.parse_text_units.side_effect = _parse
    presenter = MagicMock(name="Presenter")
    presenter.show_warning.side_effect = lambda msg: order.append(f"warn:{msg}")
    proc = _make_processor(test_config, subtitle_parser=sp, presenter=presenter)

    with patch(_IMG):
        proc.process_reading(_document([_unit(0)], warnings=["text-only volume", "unusable cover"]))

    assert order[:2] == ["warn:text-only volume", "warn:unusable cover"]
    assert order.index("parse") > 1  # both warnings drained before parsing


def test_stats_records_document_identity(test_config):
    """13. _record_session records the document's series/episode."""
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    stats = MagicMock(name="StatsService")
    stats.is_available.return_value = True
    proc = _make_processor(test_config, subtitle_parser=sp, stats_service=stats)

    with patch(_IMG):
        proc.process_reading(_document([_unit(0)], series="ShowX", episode="ep07"))

    session = stats.record_session.call_args.args[0]
    assert session.series_name == "ShowX"
    assert session.episode_name == "ep07"


def test_source_label_manga_vs_book(test_config):
    """14. Manga → sanitized 'series — episode'; book → episode (title) only."""
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})

    sp_m = MagicMock()
    sp_m.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki_m = _make_anki_service()
    _make_processor(test_config, subtitle_parser=sp_m, anki_service=anki_m).process_reading(
        _document([_unit(0, label="p.1")], kind="manga", series="My Show", episode="vol02 [JA]-Grp")
    )
    # _sanitize_source_label strips the trailing *arr metadata block.
    assert _sources(anki_m)[0] == "My Show — vol02 @ p.1"

    sp_b = MagicMock()
    sp_b.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki_b = _make_anki_service()
    _make_processor(test_config, subtitle_parser=sp_b, anki_service=anki_b).process_reading(
        _document([_unit(0, label="ch.3")], kind="book", series="ignored", episode="A Fine Novel", title="A Fine Novel")
    )
    assert _sources(anki_b)[0] == "A Fine Novel @ ch.3"


def test_preview_mode_returns_words_no_cards(test_config):
    """15. Preview mode returns the word list, creates no cards, skips phase 3'."""
    words = [_word("犬", 0), _word("猫", 1)]
    counts = collections.Counter({"犬": 1, "猫": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    with patch(_IMG) as prep:
        res = proc.process_reading(_document([_unit(0), _unit(1)]), preview_mode=True)

    prep.assert_not_called()
    anki.create_cards_batch.assert_not_called()
    assert res.cards_created == 0
    assert set(res.mined_forms) == {"犬", "猫"}


def test_partial_failure_carries_partial_ids(test_config):
    """16. Exception mid-phase-5 with partial ids → _partial_failure_result path."""
    words = [_word("犬", 0)]
    counts = collections.Counter({"犬": 1})
    sp = MagicMock()
    sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()

    def _boom(card_data, pc=None):
        anki.last_created_note_ids = [1, 2]  # partial batch landed before failure
        raise AnkiMinerException("card creation failed")

    anki.create_cards_batch.side_effect = _boom
    proc = _make_processor(test_config, subtitle_parser=sp, anki_service=anki)

    with patch(_IMG):
        res = proc.process_reading(_document([_unit(0)]))

    assert res.card_ids == [1, 2]
    assert res.cards_created == 2
    assert not res.success  # errors recorded


# --------------------------------------------------------------------------- #
# Sentence TTS (reading-only sentence audio)
# --------------------------------------------------------------------------- #
def _tts_config(test_config, **overrides):
    """test_config with sentence TTS switched on (master flag)."""
    return replace(test_config, reading_tts_enabled=True, **overrides)


def _make_sentence_fetcher(path: Path | None = Path("/tmp/tts/sentencetts_google_abc.mp3")):
    fetcher = MagicMock(name="SentenceFetcher")
    fetcher.fetch.side_effect = lambda sentence, cancelled_check=None: path
    fetcher.stats.return_value = {}
    return fetcher


class TestReadingSentenceTts:
    def test_gate_default_off_fetcher_never_called(self, test_config):
        """Default config (master OFF) never touches the fetcher; bands unchanged."""
        words = [_word("犬", 0)]
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        fetcher = _make_sentence_fetcher()
        proc = _make_processor(test_config, subtitle_parser=sp, sentence_audio_fetcher=fetcher)
        assert proc._reading_tts_active is False

        rec = _RecordingProgress()
        with patch(_IMG):
            proc.process_reading(_document([_unit(0)]), progress_callback=rec)

        fetcher.fetch.assert_not_called()
        # Band math identical to the pre-feature default (image+defs+cards).
        assert len(rec.pcts) == 3 * len(words) + (3 - 1) + 1
        assert rec.pcts[-1] == 100

    def test_gate_matrix(self, test_config):
        """Gate inactive when: master off / audio unmapped / both providers off / no fetcher."""
        fetcher = _make_sentence_fetcher()
        # master off
        proc = _make_processor(test_config, sentence_audio_fetcher=fetcher)
        assert proc._reading_tts_active is False
        # audio field unmapped
        cfg = _tts_config(test_config, anki_fields={**dict(test_config.anki_fields), "audio": ""})
        assert _make_processor(cfg, sentence_audio_fetcher=fetcher)._reading_tts_active is False
        # both providers off
        cfg = _tts_config(test_config, reading_tts_google_enabled=False, reading_tts_papago_enabled=False)
        assert _make_processor(cfg, sentence_audio_fetcher=fetcher)._reading_tts_active is False
        # fetcher missing
        assert _make_processor(_tts_config(test_config))._reading_tts_active is False
        # fully on
        assert _make_processor(_tts_config(test_config), sentence_audio_fetcher=fetcher)._reading_tts_active is True

    def test_sentence_audio_after_expression_audio(self, test_config):
        """TTS runs after images AND after expression audio; progress monotonic to 100."""
        cfg = _tts_config(
            test_config,
            anki_fields={**dict(test_config.anki_fields), "expression_audio": "ExprAudio"},
        )
        units = [_unit(0, image_ref=ImageRef(Path("/p0.png"))), _unit(1, image_ref=ImageRef(Path("/p1.png")))]
        words = [_word("犬", 0), _word("猫", 1)]
        counts = collections.Counter({"犬": 1, "猫": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        order: list[str] = []
        expr = MagicMock(name="ExprFetcher")
        expr.stats.return_value = {}
        expr.fetch_candidates.side_effect = lambda cands, cancelled_check=None: order.append("expr") or None
        tts = MagicMock(name="SentenceFetcher")
        tts.stats.return_value = {}
        tts.fetch.side_effect = lambda sentence, cancelled_check=None: order.append("tts") or None
        proc = _make_processor(cfg, subtitle_parser=sp, expression_audio_fetcher=expr, sentence_audio_fetcher=tts)

        rec = _RecordingProgress()
        with patch(_IMG, side_effect=lambda ref, dest: order.append("prep") or Path("/tmp/reading_x.jpg")):
            proc.process_reading(_document(units), progress_callback=rec)

        assert order == ["prep", "prep", "expr", "expr", "tts", "tts"]
        assert rec.pcts == sorted(rec.pcts) and rec.pcts[-1] == 100

    def test_sentence_audio_sets_media_fields(self, test_config, tmp_path):
        """A fetch hit lands on media.audio_path/audio_filename; cards carry it."""
        mp3 = tmp_path / "sentencetts_google_deadbeef.mp3"
        mp3.write_bytes(b"ID3")
        words = [_word("犬", 0)]
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        anki = _make_anki_service()
        fetcher = _make_sentence_fetcher(mp3)
        proc = _make_processor(
            _tts_config(test_config), subtitle_parser=sp, anki_service=anki, sentence_audio_fetcher=fetcher
        )

        with patch(_IMG):
            res = proc.process_reading(_document([_unit(0)]))

        assert res.cards_created == 1
        payload = anki.last_card_data[0]
        assert payload.media.audio_path == mp3
        assert payload.media.audio_filename == mp3.name

    def test_sentence_audio_failure_leaves_fields_none_cards_still_created(self, test_config):
        words = [_word("犬", 0)]
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        anki = _make_anki_service()
        fetcher = _make_sentence_fetcher(None)
        proc = _make_processor(
            _tts_config(test_config), subtitle_parser=sp, anki_service=anki, sentence_audio_fetcher=fetcher
        )

        with patch(_IMG):
            res = proc.process_reading(_document([_unit(0)]))

        assert res.cards_created == 1
        payload = anki.last_card_data[0]
        assert payload.media.audio_path is None
        assert payload.media.audio_filename is None

    def test_dedup_one_fetch_per_unique_sentence(self, test_config, tmp_path):
        """Words sharing a sentence trigger ONE synthesis; both cards share the file."""
        mp3 = tmp_path / "sentencetts_google_cafe.mp3"
        mp3.write_bytes(b"ID3")
        shared = "犬と猫の文"
        w1 = replace(_word("犬", 0), sentence=shared)
        w2 = replace(_word("猫", 0), sentence=shared)
        counts = collections.Counter({"犬": 1, "猫": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning([w1, w2], None, counts)
        anki = _make_anki_service()
        fetcher = _make_sentence_fetcher(mp3)
        # deduplicate_sentences would collapse the pair in phase 2 before TTS
        # ever sees it; disable so BOTH words reach phase 3' sharing a sentence.
        cfg = _tts_config(test_config, deduplicate_sentences=False)
        proc = _make_processor(cfg, subtitle_parser=sp, anki_service=anki, sentence_audio_fetcher=fetcher)

        with patch(_IMG):
            proc.process_reading(_document([_unit(0)]))

        assert fetcher.fetch.call_count == 1
        assert [p.media.audio_filename for p in anki.last_card_data] == [mp3.name, mp3.name]

    def test_dedup_memoizes_failures(self, test_config):
        """A failing shared sentence is fetched once, not re-hammered."""
        shared = "共有の文"
        w1 = replace(_word("犬", 0), sentence=shared)
        w2 = replace(_word("猫", 0), sentence=shared)
        counts = collections.Counter({"犬": 1, "猫": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning([w1, w2], None, counts)
        fetcher = _make_sentence_fetcher(None)
        cfg = _tts_config(test_config, deduplicate_sentences=False)
        proc = _make_processor(cfg, subtitle_parser=sp, sentence_audio_fetcher=fetcher)

        with patch(_IMG):
            proc.process_reading(_document([_unit(0)]))

        assert fetcher.fetch.call_count == 1

    def test_empty_sentence_skipped_still_ticks_progress(self, test_config):
        w = replace(_word("犬", 0), sentence="   ")
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning([w], None, counts)
        fetcher = _make_sentence_fetcher()
        proc = _make_processor(_tts_config(test_config), subtitle_parser=sp, sentence_audio_fetcher=fetcher)

        rec = _RecordingProgress()
        with patch(_IMG):
            res = proc.process_reading(_document([_unit(0)]), progress_callback=rec)

        fetcher.fetch.assert_not_called()
        assert res.cards_created == 1
        # 4 bands active (image/tts/defs/cards), every one fully consumed.
        assert rec.pcts == sorted(rec.pcts) and rec.pcts[-1] == 100
        assert len(rec.pcts) == 4 * 1 + (4 - 1) + 1

    def test_band_consumed_when_zero_words(self, test_config):
        """Active gate + zero mineable words: band still consumed, progress sane."""
        counts: collections.Counter = collections.Counter()
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning([], None, counts)
        fetcher = _make_sentence_fetcher()
        proc = _make_processor(_tts_config(test_config), subtitle_parser=sp, sentence_audio_fetcher=fetcher)

        rec = _RecordingProgress()
        with patch(_IMG):
            res = proc.process_reading(_document([_unit(0)]), progress_callback=rec)

        fetcher.fetch.assert_not_called()
        assert res.cards_created == 0

    def test_cancellation_mid_loop_completes_band(self, test_config):
        """Cancel during the TTS loop: on_complete fires, run reports cancelled."""
        words = [_word("犬", 0), _word("猫", 1)]
        counts = collections.Counter({"犬": 1, "猫": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        fetcher = MagicMock(name="SentenceFetcher")
        fetcher.stats.return_value = {}
        proc = _make_processor(_tts_config(test_config), subtitle_parser=sp, sentence_audio_fetcher=fetcher)

        def _fetch_and_cancel(sentence, cancelled_check=None):
            proc.cancel()
            return None

        fetcher.fetch.side_effect = _fetch_and_cancel

        with patch(_IMG):
            res = proc.process_reading(_document([_unit(0), _unit(1)]))

        assert fetcher.fetch.call_count == 1  # second word never fetched
        assert res.cards_created == 0
        assert not res.success

    def test_tts_reads_post_phase2_sentence(self, test_config):
        """Invariant: TTS synthesizes the FINAL word.sentence (post i+1/curation swap)."""
        cfg = _tts_config(test_config, use_i_plus_one_filter=True)
        words = [_word("犬", 0)]
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        fetched: list[str] = []
        fetcher = MagicMock(name="SentenceFetcher")
        fetcher.stats.return_value = {}
        fetcher.fetch.side_effect = lambda sentence, cancelled_check=None: fetched.append(sentence) or None
        proc = _make_processor(cfg, subtitle_parser=sp, sentence_audio_fetcher=fetcher)
        # Simulate the i+1 swap re-stamping the card sentence in phase 2.
        swapped = replace(words[0], sentence="i+1で入れ替えた文")
        proc.word_filter.filter_i_plus_one = MagicMock(return_value=[swapped])

        with patch(_IMG):
            proc.process_reading(_document([_unit(0)]))

        assert fetched == ["i+1で入れ替えた文"]

    def test_aggregated_summary_counts_unique_sentences(self, test_config):
        """Info line reports hits/unique-sentences; warning uses len(memo) denominator."""
        shared = "共有の文"
        w1 = replace(_word("犬", 0), sentence=shared)
        w2 = replace(_word("猫", 0), sentence=shared)
        w3 = _word("鳥", 1)
        counts = collections.Counter({"犬": 1, "猫": 1, "鳥": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning([w1, w2, w3], None, counts)
        presenter = MagicMock(name="Presenter")
        fetcher = MagicMock(name="SentenceFetcher")
        fetcher.fetch.side_effect = lambda sentence, cancelled_check=None: None
        # Both unique sentences failed with connection errors -> dominant cause.
        fetcher.stats.return_value = {"ssl": 0, "connection": 2, "timeout": 0, "http_status": 0, "non_audio": 0}
        cfg = _tts_config(test_config, deduplicate_sentences=False)
        proc = _make_processor(cfg, subtitle_parser=sp, presenter=presenter, sentence_audio_fetcher=fetcher)

        with patch(_IMG):
            proc.process_reading(_document([_unit(0), _unit(1)]))

        infos = [c.args[0] for c in presenter.show_info.call_args_list]
        assert "Sentence audio: 0/2 sentences" in infos  # unique sentences, not 3 words
        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("Sentence-audio TTS connection" in w for w in warnings)

    def test_preview_mode_skips_tts(self, test_config):
        words = [_word("犬", 0)]
        counts = collections.Counter({"犬": 1})
        sp = MagicMock()
        sp.parse_text_units.side_effect = _parse_returning(words, None, counts)
        fetcher = _make_sentence_fetcher()
        proc = _make_processor(_tts_config(test_config), subtitle_parser=sp, sentence_audio_fetcher=fetcher)

        with patch(_IMG):
            proc.process_reading(_document([_unit(0)]), preview_mode=True)

        fetcher.fetch.assert_not_called()
