from pathlib import Path

from anki_miner.services.audio_fetch_common import find_cached_by_stem


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
