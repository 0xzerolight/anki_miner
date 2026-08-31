"""extract_media_batch returns results in caller input order, not completion order."""

import time
from dataclasses import replace
from unittest.mock import patch

from anki_miner.models import MediaData
from anki_miner.services.media_extractor import MediaExtractorService


def _service(test_config):
    # Force a real pool: with max_parallel_workers=1 completion order would
    # equal input order and the assertion would be vacuous.
    return MediaExtractorService(replace(test_config, max_parallel_workers=6))


def _inverted_latency_extract(words, tmp_path, *, fail_lemmas=()):
    """side_effect whose per-word sleep makes completion order the reverse of input order."""
    delays = {word.lemma: (len(words) - index) * 0.05 for index, word in enumerate(words)}

    def fake_extract(video_file, word, temp_folder=None, **kwargs):
        time.sleep(delays[word.lemma])
        if word.lemma in fail_lemmas:
            return MediaData()
        shot = tmp_path / f"{word.lemma}.jpg"
        shot.write_bytes(b"\xff\xd8fake")
        return MediaData(screenshot_path=shot, screenshot_filename=shot.name)

    return fake_extract


def test_batch_returns_input_order_not_completion_order(test_config, make_tokenized_word, tmp_path):
    service = _service(test_config)
    words = [make_tokenized_word(surface=f"語{i}", lemma=f"語{i}", start_time=float(i)) for i in range(6)]

    with patch.object(service, "extract_media", side_effect=_inverted_latency_extract(words, tmp_path)):
        results = service.extract_media_batch(tmp_path / "episode.mkv", words, include_audio=False)

    assert [word.lemma for word, _ in results] == [word.lemma for word in words]


def test_dropped_words_leave_the_rest_in_input_order(test_config, make_tokenized_word, tmp_path):
    """A word whose screenshot fails is removed, not reordered around."""
    service = _service(test_config)
    words = [make_tokenized_word(surface=f"語{i}", lemma=f"語{i}", start_time=float(i)) for i in range(6)]

    with patch.object(
        service,
        "extract_media",
        side_effect=_inverted_latency_extract(words, tmp_path, fail_lemmas=("語1", "語3")),
    ):
        results = service.extract_media_batch(tmp_path / "episode.mkv", words, include_audio=False)

    assert [word.lemma for word, _ in results] == ["語0", "語2", "語4", "語5"]
