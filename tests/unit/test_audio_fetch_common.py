from pathlib import Path
from unittest.mock import MagicMock

from anki_miner.services.audio_fetch_common import download_audio_to_cache, find_cached_by_stem, new_failure_counts


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
