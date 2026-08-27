from pathlib import Path
from unittest.mock import MagicMock

from anki_miner.models import TokenizedWord
from anki_miner.services.audio_fetch_common import (
    download_audio_to_cache,
    expression_audio_candidates,
    find_cached_by_stem,
    new_failure_counts,
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
