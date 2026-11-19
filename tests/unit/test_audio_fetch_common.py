import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models import TokenizedWord
from anki_miner.services import audio_fetch_common
from anki_miner.services.audio_fetch_common import (
    download_audio_to_cache,
    expression_audio_candidates,
    find_cached_by_stem,
    log_fetch_outcome,
    new_failure_counts,
    record_cached_path,
    reset_fetch_outcome_rate_limit,
)


def _word(**kwargs) -> TokenizedWord:
    base = {
        "surface": "",
        "lemma": "",
        "reading": "",
        "sentence": "",
        "start_time": 0.0,
        "end_time": 0.0,
        "duration": 0.0,
    }
    base.update(kwargs)
    return TokenizedWord(**base)


def test_cached_audio_lookup_sublinear(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    expected = []
    for index in range(32):
        path = cache_dir / f"word{index}.mp3"
        path.write_bytes(b"ID3")
        expected.append(path)

    scans = 0
    real_iterdir = Path.iterdir

    def _counted_iterdir(path):
        nonlocal scans
        if path == cache_dir:
            scans += 1
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", _counted_iterdir)

    assert [find_cached_by_stem(cache_dir, f"word{i}") for i in range(32)] == expected
    assert scans == 1


def _replace_cache_dir(cache_dir: Path) -> None:
    # What purge_pack_cache does: rename the directory away, then delete it.
    gone = cache_dir.with_name(f".{cache_dir.name}.purge")
    cache_dir.rename(gone)
    for path in gone.iterdir():
        path.unlink()
    gone.rmdir()


def test_record_after_directory_replacement_drops_purged_entries(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "a.mp3").write_bytes(b"ID3")
    (cache_dir / "b.mp3").write_bytes(b"ID3")
    assert find_cached_by_stem(cache_dir, "a") == cache_dir / "a.mp3"

    _replace_cache_dir(cache_dir)
    cache_dir.mkdir()
    new_path = cache_dir / "c.mp3"
    new_path.write_bytes(b"ID3")
    record_cached_path(cache_dir, new_path)

    assert find_cached_by_stem(cache_dir, "a") is None
    assert find_cached_by_stem(cache_dir, "c") == new_path


def test_lookup_in_a_missing_directory_forgets_its_index(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "a.mp3").write_bytes(b"ID3")
    assert find_cached_by_stem(cache_dir, "a") == cache_dir / "a.mp3"

    _replace_cache_dir(cache_dir)
    assert find_cached_by_stem(cache_dir, "a") is None
    cache_dir.mkdir()
    new_path = cache_dir / "c.mp3"
    new_path.write_bytes(b"ID3")
    # Even if the recreated directory reused the old inode, nothing is merged.
    record_cached_path(cache_dir, new_path)

    assert cache_dir not in audio_fetch_common._AUDIO_CACHE_INDEXES
    assert find_cached_by_stem(cache_dir, "a") is None


def test_mp3_mime_with_html_body_is_rejected(tmp_path):
    response = MagicMock(status_code=200, headers={"Content-Type": "audio/mpeg"})
    response.iter_content.side_effect = lambda chunk_size=8192: iter([b"<html>rate limited</html>"])
    session = MagicMock()
    session.get.return_value = response
    counts = new_failure_counts()

    result = download_audio_to_cache(session, "https://example.test/audio", tmp_path, "term", failure_counts=counts)

    assert result is None
    assert counts["non_audio"] == 1
    assert list(tmp_path.iterdir()) == []
    response.close.assert_called_once_with()


def test_cancel_between_chunks_aborts_without_cache_commit(tmp_path):
    cancelled = False

    def _chunks(chunk_size=8192):
        nonlocal cancelled
        yield b"ID3audio"
        cancelled = True
        yield b"more-audio"

    response = MagicMock(status_code=200, headers={"Content-Type": "audio/mpeg"})
    response.iter_content.side_effect = _chunks
    session = MagicMock()
    session.get.return_value = response
    counts = new_failure_counts()

    result = download_audio_to_cache(
        session,
        "https://example.test/audio",
        tmp_path,
        "term",
        failure_counts=counts,
        cancelled_check=lambda: cancelled,
    )

    assert result is None
    assert counts == new_failure_counts()
    assert list(tmp_path.iterdir()) == []
    response.close.assert_called_once_with()


def test_candidates_plain_word_is_one_pair():
    word = _word(surface="食べる", lemma="食べる", expression_reading="たべる")
    assert expression_audio_candidates(word) == [("食べる", "たべる")]


def test_candidates_katakana_kanji_adds_katakana_reading_variant():
    word = _word(surface="チップ", lemma="チップ", expression_reading="ちっぷ")
    assert expression_audio_candidates(word) == [("チップ", "ちっぷ"), ("チップ", "チップ")]


def test_candidates_okurigana_only_lemma_is_appended():
    word = _word(surface="探し", lemma="探す", expression_reading="さがし", lemma_reading="さがす")
    assert expression_audio_candidates(word) == [("探し", "さがし"), ("探す", "さがす")]


def test_candidates_different_kanji_lemma_is_excluded():
    # 殺る → 遣る is a UniDic canonicalization onto another homograph.
    word = _word(surface="殺る", lemma="遣る", expression_reading="やる", lemma_reading="やる")
    assert expression_audio_candidates(word) == [("殺る", "やる")]


def test_candidates_blank_reading_is_dropped():
    word = _word(surface="食べる", lemma="食べる", expression_reading="")
    assert expression_audio_candidates(word) == []


# ---------------------------------------------------------------------------
# log_fetch_outcome (per-source HTTP outcome choke point)
# ---------------------------------------------------------------------------

_OUTCOME_LOGGER = "anki_miner.services.audio_fetch_common"


@pytest.fixture(autouse=True)
def _reset_fetch_outcome_counters():
    """Rate-limit state is process-wide; isolate every test in this module."""
    reset_fetch_outcome_rate_limit()
    yield
    reset_fetch_outcome_rate_limit()


def test_first_outcome_for_a_source_warns_with_every_field(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x", status=404, reason="http_status")

    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "Audio fetch: source=jpod101 word=猫 reading=ねこ status=404 reason=http_status url=https://a.test/x"
    )


def test_second_outcome_for_the_same_source_and_reason_is_debug(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x", status=404, reason="http_status")
        log_fetch_outcome(log, "jpod101", "犬", "いぬ", "https://a.test/y", status=404, reason="http_status")

    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.DEBUG]
    assert "word=犬" in caplog.records[-1].getMessage()


def test_a_different_reason_gets_its_own_first_warning(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x", status=404, reason="http_status")
        log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x", reason="transport")

    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.WARNING]


def test_every_hundredth_outcome_warns_with_the_running_total(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        for _ in range(100):
            log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x", status=404, reason="http_status")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "occurrences=100" in warnings[-1].getMessage()


def test_custom_source_url_query_is_redacted(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        log_fetch_outcome(
            log, "custom_json", "猫", "ねこ", "https://a.test/x?key=SECRET", status=500, reason="http_status"
        )

    message = caplog.records[-1].getMessage()
    assert "url=https://a.test/x" in message
    assert "SECRET" not in message


def test_non_custom_source_url_is_logged_verbatim(caplog):
    log = logging.getLogger(_OUTCOME_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        log_fetch_outcome(log, "jpod101", "猫", "ねこ", "https://a.test/x?kanji=猫", reason="transport")

    assert "url=https://a.test/x?kanji=猫" in caplog.records[-1].getMessage()


def test_download_helper_logs_the_http_status_outcome(tmp_path, caplog):
    response = MagicMock(status_code=503, headers={})
    session = MagicMock()
    session.get.return_value = response

    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        result = download_audio_to_cache(
            session, "https://example.test/audio", tmp_path, "term", source="custom", word="猫", reading="ねこ"
        )

    assert result is None
    message = caplog.records[-1].getMessage()
    assert "source=custom" in message
    assert "word=猫" in message
    assert "status=503" in message
    assert "reason=http_status" in message


def test_download_helper_logs_an_unknown_content_type_with_the_body_size(tmp_path, caplog):
    response = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    response.iter_content.side_effect = lambda chunk_size=8192: iter([b"<html>nope</html>"])
    session = MagicMock()
    session.get.return_value = response

    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        download_audio_to_cache(session, "https://example.test/audio", tmp_path, "term", source="papago", word="stem")

    message = caplog.records[-1].getMessage()
    assert "reason=unknown_content_type" in message
    assert "content_type=text/html" in message
    assert "bytes=17" in message


def test_download_helper_logs_the_transport_failure_with_type_and_message(tmp_path, caplog):
    session = MagicMock()
    session.get.side_effect = OSError("socket exploded")

    with caplog.at_level(logging.DEBUG, logger=_OUTCOME_LOGGER):
        download_audio_to_cache(session, "https://example.test/audio", tmp_path, "term", source="custom", word="猫")

    message = caplog.records[-1].getMessage()
    assert "reason=transport" in message
    assert 'error="OSError: socket exploded"' in message
