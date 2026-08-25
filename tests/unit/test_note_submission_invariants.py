"""Cross-layer invariants for confirmed Anki note submission."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from anki_miner.models import CANCELLED_ERROR, CardPayload, MediaData, TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor, _EpisodeContext
from anki_miner.services.anki_service import AnkiService


def _word(index: int) -> TokenizedWord:
    expression = f"語{index}"
    return TokenizedWord(
        surface=expression,
        lemma=expression,
        reading="ゴ",
        sentence=f"{expression}。",
        start_time=float(index),
        end_time=float(index + 1),
        duration=1.0,
        pos="名詞",
    )


def test_stop_after_first_confirmed_batch_preserves_only_committed_ids_and_forms(test_config):
    service = AnkiService(test_config)
    words = [_word(i) for i in range(201)]
    media_results = [(word, MediaData()) for word in words]
    cancel_event = threading.Event()
    progress = MagicMock()
    progress.on_progress.side_effect = lambda *_args: cancel_event.set()
    definition_service = MagicMock()
    definition_service.css_entries.return_value = []
    processor = EpisodeProcessor(
        test_config,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        definition_service,
        service,
        MagicMock(),
    )
    processor._external_cancel = cancel_event.is_set
    ctx = _EpisodeContext(
        start_time=time.time(),
        video_file_str="episode.mkv",
        subtitle_file_str="episode.ass",
        episode_name="episode",
        series_name="series",
        source_label="episode",
    )
    next_id = 1

    def add_notes(_url, action, *, params, timeout):
        nonlocal next_id
        assert action == "addNotes"
        count = len(params["notes"])
        ids = list(range(next_id, next_id + count))
        next_id += count
        return ids

    with (
        patch.object(service, "_probe_duplicates", side_effect=lambda notes: [False] * len(notes)),
        patch.object(service, "_store_media_files_batch", return_value=set()),
        patch.object(service, "_upload_dict_media_batch"),
        patch("anki_miner.services.anki_service.post_action", side_effect=add_notes) as post,
    ):
        cards_created, note_ids, mined_forms_for_undo = processor._phase5_create(
            ctx,
            media_results,
            ["definition"] * len(words),
            [None] * len(words),
            [(None, None)] * len(words),
            progress,
        )

    assert post.call_count == 1
    assert cards_created == 100
    assert note_ids == list(range(1, 101))
    assert service.last_created_mined_forms == [word.mined_form for word in words[:100]]
    assert mined_forms_for_undo == []
    assert ctx.errors == [CANCELLED_ERROR]


def test_duplicate_probe_rejects_payload_before_any_of_its_media_uploads(test_config):
    service = AnkiService(test_config)
    duplicate = CardPayload(word=_word(0), media=MediaData(), definition="duplicate")
    survivor = CardPayload(word=_word(1), media=MediaData(), definition="survivor")

    with (
        patch.object(service, "_probe_duplicates", return_value=[True, False]),
        patch.object(service, "_store_media_files_batch", return_value=set()) as store_media,
        patch.object(service, "_upload_dict_media_batch") as store_dict_media,
        patch("anki_miner.services.anki_service.post_action", return_value=[42]),
    ):
        assert service.create_cards_batch([duplicate, survivor]) == [42]

    assert store_media.call_args.args[0] == [survivor]
    assert store_dict_media.call_args.args[0] == [survivor]
