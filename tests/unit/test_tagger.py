"""Tests for anki_miner.services.tagger — shared singleton and pre-warm."""

import threading
from unittest.mock import MagicMock

import pytest

import anki_miner.services.tagger as tagger_mod
from anki_miner.services.tagger import get_shared_tagger, prewarm_tagger


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-global _tagger to None before and after each test.

    The singleton is process-wide; without this reset, test ordering would
    affect outcomes and the "built-once" concurrency test would be moot.
    """
    tagger_mod._tagger = None
    yield
    tagger_mod._tagger = None


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


class TestIdentity:
    """get_shared_tagger() returns the same object on every call."""

    def test_same_object_across_calls(self, monkeypatch):
        """Two successive calls return the identical instance."""
        fake_tagger = MagicMock(name="fake_tagger")
        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: fake_tagger)

        first = get_shared_tagger()
        second = get_shared_tagger()
        assert first is second

    def test_singleton_stored_in_module(self, monkeypatch):
        """After the first call the module-level _tagger holds the instance."""
        fake_tagger = MagicMock(name="fake_tagger")
        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: fake_tagger)

        result = get_shared_tagger()
        assert tagger_mod._tagger is result


class TestBuiltOnceUnderConcurrency:
    """Constructor is invoked exactly once even with heavy thread contention."""

    def test_factory_called_once(self, monkeypatch):
        """All threads get the same object; Tagger() is called exactly once."""
        call_count = 0
        returned_instance = MagicMock(name="shared_tagger")

        def counting_factory():
            nonlocal call_count
            call_count += 1
            return returned_instance

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", counting_factory)

        n_threads = 32
        barrier = threading.Barrier(n_threads)
        results: list[object] = [None] * n_threads

        def worker(idx: int):
            barrier.wait()  # all threads release simultaneously to maximise contention
            results[idx] = get_shared_tagger()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert call_count == 1, f"Tagger() was called {call_count} times; expected 1"
        for i, r in enumerate(results):
            assert r is returned_instance, f"Thread {i} got a different object"


class TestPrewarm:
    """prewarm_tagger() is non-blocking and triggers construction."""

    def test_returns_thread_immediately(self, monkeypatch):
        """prewarm_tagger() returns a Thread without blocking."""
        barrier = threading.Barrier(2)
        fake_tagger = MagicMock(name="fake_tagger")

        def slow_factory():
            barrier.wait()  # block until the test joins the barrier
            return fake_tagger

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", slow_factory)

        t = prewarm_tagger()
        assert isinstance(t, threading.Thread)
        assert t.daemon is True
        # unblock the factory so the thread can finish
        barrier.wait()
        t.join(timeout=5)

    def test_tagger_built_after_join(self, monkeypatch):
        """After the pre-warm thread joins, get_shared_tagger() returns the built instance."""
        call_count = 0
        fake_tagger = MagicMock(name="fake_tagger")

        def counting_factory():
            nonlocal call_count
            call_count += 1
            return fake_tagger

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", counting_factory)

        t = prewarm_tagger()
        t.join(timeout=10)

        result = get_shared_tagger()
        assert result is fake_tagger
        assert call_count == 1, "Factory should have been called exactly once"

    def test_subsequent_get_does_not_rebuild(self, monkeypatch):
        """Calling get_shared_tagger() after prewarm does not call the factory again."""
        call_count = 0
        fake_tagger = MagicMock(name="fake_tagger")

        def counting_factory():
            nonlocal call_count
            call_count += 1
            return fake_tagger

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", counting_factory)

        t = prewarm_tagger()
        t.join(timeout=10)

        get_shared_tagger()
        get_shared_tagger()

        assert call_count == 1


class TestFailurePaths:
    """Error handling contracts for get_shared_tagger and prewarm_tagger."""

    def test_get_shared_tagger_does_not_poison_on_failure(self, monkeypatch):
        """Constructor failure leaves _tagger None so a retry can still succeed."""
        call_count = 0
        fake_tagger = MagicMock(name="fake_tagger")

        def flaky_factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("MeCab init failed")
            return fake_tagger

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", flaky_factory)

        with pytest.raises(RuntimeError, match="MeCab init failed"):
            get_shared_tagger()

        assert tagger_mod._tagger is None, "_tagger must remain None after a failed build"

        result = get_shared_tagger()
        assert result is fake_tagger
        assert call_count == 2, "Constructor should have been called twice (once failing, once succeeding)"

    def test_prewarm_swallows_failure(self, monkeypatch, caplog):
        """A broken install on the prewarm thread must not raise on the calling thread."""
        import logging

        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: (_ for _ in ()).throw(RuntimeError("no MeCab")))

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.tagger"):
            t = prewarm_tagger()
            t.join(timeout=10)

        assert tagger_mod._tagger is None, "_tagger must remain None when prewarm fails"
        assert any(
            "pre-warm failed" in r.message for r in caplog.records
        ), "Expected a warning log about prewarm failure"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestRealTagger:
    """Smoke test against a real fugashi.Tagger (skipped if not installed)."""

    def test_real_tagger_is_callable(self):
        """get_shared_tagger() returns something that can parse a short string."""
        tagger = get_shared_tagger()
        # fugashi.Tagger is callable; calling it tokenises a string
        tokens = tagger("食べる")
        assert tokens is not None
        # Expect at least one token for a verb
        assert len(tokens) >= 1
