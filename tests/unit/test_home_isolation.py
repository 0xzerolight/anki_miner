"""Tests for the shared home-isolation primitives in ``tests._home_isolation``.

These cover the two helpers the standalone (non-pytest) E2E runner needs:
``set_test_home`` (env var + in-process binding patches in one call) and the
``guard_real_home`` context-manager tripwire. The existing conftest fixtures are
exercised implicitly by the rest of the suite; here we prove the extracted
helpers redirect a freshly-constructed ``AnkiMinerConfig`` and that the guard
fires only on real mutation.

The autouse conftest isolation fixtures are active during these tests, so
``set_test_home`` patches OVER the per-test home; we always restore via the
returned saved triples + the saved env var in a ``finally`` so no isolation
state leaks into sibling tests.
"""

import os

import pytest

from tests._home_isolation import (
    guard_real_home,
    restore_home_patches,
    set_test_home,
    snapshot_home,
)


def test_set_test_home_redirects_config_defaults(tmp_path):
    """After ``set_test_home(tmp)``, a fresh ``AnkiMinerConfig`` lands every
    home-derived path under ``tmp`` — proving the in-process binding patches
    (not just the env var) took effect for ``config.config``'s snapshot."""
    from anki_miner.config import AnkiMinerConfig

    env_was_set = "ANKI_MINER_HOME" in os.environ
    env_prev = os.environ.get("ANKI_MINER_HOME")
    saved = set_test_home(tmp_path)
    try:
        assert os.environ["ANKI_MINER_HOME"] == str(tmp_path)

        config = AnkiMinerConfig()
        assert config.known_words_db_path == tmp_path / "known_words.db"
        assert config.stats_db_path == tmp_path / "stats.db"
        assert config.dicts_root == tmp_path / "dicts"
    finally:
        restore_home_patches(saved)
        if env_was_set:
            os.environ["ANKI_MINER_HOME"] = env_prev
        else:
            os.environ.pop("ANKI_MINER_HOME", None)


def test_set_test_home_safe_when_env_already_set(tmp_path):
    """``set_test_home`` is belt-and-suspenders: the runner sets the env var
    pre-import, then calls this. Calling it with the var already set must not
    error and must (re)assert the value."""
    env_was_set = "ANKI_MINER_HOME" in os.environ
    env_prev = os.environ.get("ANKI_MINER_HOME")
    os.environ["ANKI_MINER_HOME"] = str(tmp_path)
    saved = set_test_home(tmp_path)
    try:
        assert os.environ["ANKI_MINER_HOME"] == str(tmp_path)
    finally:
        restore_home_patches(saved)
        if env_was_set:
            os.environ["ANKI_MINER_HOME"] = env_prev
        else:
            os.environ.pop("ANKI_MINER_HOME", None)


def test_guard_real_home_passes_when_unchanged(tmp_path):
    """No mutation under the watched dir -> the context manager exits cleanly."""
    watched = tmp_path / "watched"
    watched.mkdir()
    with guard_real_home(watched):
        pass  # touch nothing


def test_guard_real_home_passes_when_dir_absent(tmp_path):
    """Absent-before / absent-after is fine; the guard must never create it."""
    watched = tmp_path / "never_created"
    with guard_real_home(watched):
        pass
    assert not watched.exists()


def test_guard_real_home_raises_on_created_file(tmp_path):
    """A file appearing under the watched dir trips the guard with a
    ``created:`` detail in the message."""
    watched = tmp_path / "watched"
    watched.mkdir()
    with pytest.raises(AssertionError) as excinfo, guard_real_home(watched):
        (watched / "leaked.json").write_text("{}", encoding="utf-8")
    msg = str(excinfo.value)
    assert "created:" in msg
    assert str(watched) in msg


def test_guard_real_home_raises_on_modified_file(tmp_path):
    """An in-place content change to an existing watched file trips the guard
    with a ``modified:`` detail."""
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "config.json"
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError) as excinfo, guard_real_home(watched):
        target.write_text('{"changed": true}', encoding="utf-8")
    assert "modified:" in str(excinfo.value)


_OLD_NS = 1_000_000_000_000_000_000  # 2001: far from "now", so any new write moves an mtime


def _real_like_home(tmp_path):
    """A home with a state file plus the two bulk stores, every dir mtime aged so a
    write in the same coarse clock tick as setup still moves it."""
    home = tmp_path / ".anki_miner"
    (home / "profiles").mkdir(parents=True)
    (home / "gui_config.json").write_text("{}", encoding="utf-8")
    (home / "audio_cache" / "jpod101").mkdir(parents=True)
    (home / "audio_cache" / "jpod101" / "jpod101_a_a.mp3").write_bytes(b"x")
    (home / "uv" / "python" / "lib").mkdir(parents=True)
    (home / "uv" / "python" / "lib" / "site.py").write_text("", encoding="utf-8")
    for dirpath, _dirnames, _filenames in os.walk(home):
        os.utime(dirpath, ns=(_OLD_NS, _OLD_NS))
    return home


def test_snapshot_skips_files_inside_bulk_stores(tmp_path):
    """The uv tree and the audio caches hold tens of thousands of files on a real
    install; stat-ing them twice per test made the local suite ~12x slower."""
    home = _real_like_home(tmp_path)
    keys = snapshot_home(home)
    assert str(home / "gui_config.json") in keys
    assert str(home / "audio_cache" / "jpod101" / "jpod101_a_a.mp3") not in keys
    assert str(home / "uv" / "python" / "lib" / "site.py") not in keys


def test_guard_real_home_raises_on_file_created_in_a_cache_dir(tmp_path):
    """A leaking audio fetcher writing a new cache file still trips the guard,
    through the cache dir's mtime."""
    home = _real_like_home(tmp_path)
    with pytest.raises(AssertionError) as excinfo, guard_real_home(home):
        (home / "audio_cache" / "jpod101" / "jpod101_b_b.mp3").write_bytes(b"y")
    assert str(home / "audio_cache" / "jpod101") in str(excinfo.value)


def test_guard_real_home_raises_on_file_deleted_from_a_cache_dir(tmp_path):
    home = _real_like_home(tmp_path)
    with pytest.raises(AssertionError), guard_real_home(home):
        (home / "audio_cache" / "jpod101" / "jpod101_a_a.mp3").unlink()


def test_guard_real_home_raises_on_new_cache_source_dir(tmp_path):
    """A new per-source cache dir (e.g. the first Wikimedia hit) moves the store's mtime."""
    home = _real_like_home(tmp_path)
    with pytest.raises(AssertionError), guard_real_home(home):
        (home / "audio_cache" / "wikimedia").mkdir()
