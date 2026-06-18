"""Tests for anki_miner.services.tagger — shared singleton and LockedTagger."""

import threading
import time
from unittest.mock import MagicMock

import pytest

import anki_miner.services.tagger as tagger_mod
from anki_miner.services.tagger import LockedTagger, get_shared_tagger


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-global _tagger and _locked_tagger before and after each test.

    The singletons are process-wide; without this reset, test ordering would
    affect outcomes and the "built-once" concurrency tests would be moot.
    """
    tagger_mod._tagger = None
    tagger_mod._locked_tagger = None
    yield
    tagger_mod._tagger = None
    tagger_mod._locked_tagger = None


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


class TestIdentity:
    """get_shared_tagger() returns the same LockedTagger wrapper on every call."""

    def test_same_object_across_calls(self, monkeypatch):
        """Two successive calls return the identical LockedTagger instance."""
        fake_tagger = MagicMock(name="fake_tagger")
        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: fake_tagger)

        first = get_shared_tagger()
        second = get_shared_tagger()
        assert first is second

    def test_returns_locked_tagger_instance(self, monkeypatch):
        """get_shared_tagger() returns a LockedTagger, not the raw fugashi.Tagger."""
        fake_tagger = MagicMock(name="fake_tagger")
        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: fake_tagger)

        result = get_shared_tagger()
        assert isinstance(result, LockedTagger)

    def test_singleton_stored_in_module(self, monkeypatch):
        """After the first call, the module-level _locked_tagger holds the wrapper."""
        fake_tagger = MagicMock(name="fake_tagger")
        monkeypatch.setattr(tagger_mod.fugashi, "Tagger", lambda: fake_tagger)

        result = get_shared_tagger()
        assert tagger_mod._locked_tagger is result


class TestBuiltOnceUnderConcurrency:
    """Constructor is invoked exactly once even with heavy thread contention."""

    def test_factory_called_once(self, monkeypatch):
        """All threads get the same LockedTagger; Tagger() is called exactly once."""
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
            assert isinstance(r, LockedTagger), f"Thread {i} got {type(r)}, expected LockedTagger"
            assert r is results[0], f"Thread {i} got a different wrapper instance"


class TestFailurePaths:
    """Error handling contracts for get_shared_tagger."""

    def test_get_shared_tagger_does_not_poison_on_failure(self, monkeypatch):
        """Constructor failure leaves _locked_tagger None so a retry can succeed."""
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

        assert tagger_mod._locked_tagger is None, "_locked_tagger must remain None after a failed build"

        result = get_shared_tagger()
        assert isinstance(result, LockedTagger)
        assert call_count == 2, "Constructor should have been called twice (once failing, once succeeding)"


class TestLockedTaggerWrapper:
    """Unit tests for the LockedTagger wrapper itself."""

    def _make_locked(self, tokens=None):
        """Return a (LockedTagger, fake_inner) pair."""
        fake_inner = MagicMock(name="inner_tagger")
        if tokens is not None:
            fake_inner.return_value = tokens
        return LockedTagger(fake_inner), fake_inner

    def test_call_delegates_to_inner(self):
        """__call__ passes text to the inner tagger and returns its result."""
        nodes = [MagicMock(), MagicMock()]
        locked, fake = self._make_locked(tokens=nodes)

        result = locked("食べる")

        fake.assert_called_once_with("食べる")
        assert result is nodes

    def test_call_result_is_listable(self):
        """list(tagger(text)) works — callers use this pattern."""
        nodes = [MagicMock(name="node1"), MagicMock(name="node2")]
        locked, _ = self._make_locked(tokens=nodes)

        assert list(locked("食べる")) == nodes

    def test_parse_delegates_to_inner(self):
        """parse() acquires lock and delegates to inner.parse."""
        locked, fake = self._make_locked()
        fake.parse.return_value = "parsed"

        result = locked.parse("食べる")

        fake.parse.assert_called_once_with("食べる")
        assert result == "parsed"

    def test_getattr_delegates_unknown_attrs(self):
        """Other attribute access (e.g. .dictionary) is delegated to inner."""
        locked, fake = self._make_locked()
        fake.dictionary = "unidic-lite"

        assert locked.dictionary == "unidic-lite"

    def test_repr_includes_inner(self):
        """repr shows that a LockedTagger wraps the inner instance."""
        locked, _ = self._make_locked()
        assert "LockedTagger" in repr(locked)


class TestLockedTaggerConcurrencySafety:
    """Verify that LockedTagger serialises concurrent parses.

    The stub tagger increments a shared re-entry counter on every __call__
    entry and decrements it on exit.  If the counter ever exceeds 1 two threads
    have entered simultaneously — that is the bug we are preventing.
    """

    def test_no_concurrent_entry_across_n_threads(self):
        """Re-entry counter never exceeds 1 under N concurrent callers."""
        max_concurrent = 0
        concurrent_count = 0
        lock_for_counters = threading.Lock()
        violation_observed = False

        class SerializationStub:
            """Fake inner tagger that checks mutual exclusion."""

            def __call__(self, text, *args, **kwargs):
                nonlocal max_concurrent, concurrent_count, violation_observed
                with lock_for_counters:
                    concurrent_count += 1
                    if concurrent_count > 1:
                        violation_observed = True
                    if concurrent_count > max_concurrent:
                        max_concurrent = concurrent_count
                # Simulate real parse work so threads overlap in time.
                time.sleep(0.005)
                with lock_for_counters:
                    concurrent_count -= 1
                return []

        # Build a LockedTagger with its own fresh lock so this test is
        # isolated from the singleton's lock state.
        locked = LockedTagger.__new__(LockedTagger)
        object.__setattr__(locked, "_inner", SerializationStub())
        # Override the class-level lock with a fresh instance for test isolation.
        object.__setattr__(locked, "_parse_lock", threading.RLock())
        # Monkey-patch the methods to use the instance's _parse_lock.
        # Actually, re-bind via a subclass approach — simpler: just use the
        # module-level approach of sharing the class RLock (it IS per-instance via
        # the class attribute for LockedTagger; but we want test isolation).
        # Re-do: use a fresh LockedTagger instance directly — the class-level RLock
        # IS shared across ALL instances by design.  For this test we want a fresh lock.
        # Simplest: patch the class temporarily.
        original_lock = LockedTagger._parse_lock
        fresh_lock = threading.RLock()
        LockedTagger._parse_lock = fresh_lock  # type: ignore[attr-defined]
        try:
            locked = LockedTagger(SerializationStub())  # type: ignore[arg-type]

            n_threads = 20
            barrier = threading.Barrier(n_threads)

            def worker():
                barrier.wait()
                locked("テスト")

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            LockedTagger._parse_lock = original_lock  # type: ignore[attr-defined]

        assert not violation_observed, f"Concurrent entry detected! max_concurrent={max_concurrent}"
        assert max_concurrent == 1, f"Expected max_concurrent=1, got {max_concurrent}"

    def test_same_thread_reentry_does_not_deadlock(self):
        """RLock allows same-thread re-entry (e.g. nested parse calls)."""
        calls = []

        class ReentrantStub:
            def __init__(self, locked_ref):
                self._locked = locked_ref

            def __call__(self, text, *args, **kwargs):
                calls.append(text)
                if text == "outer":
                    # Re-enter the locked tagger from the same thread.
                    self._locked("inner")
                return []

        # Build with a fresh class-level lock slot (via fresh RLock patch).
        original_lock = LockedTagger._parse_lock
        LockedTagger._parse_lock = threading.RLock()  # type: ignore[attr-defined]
        try:
            stub = ReentrantStub.__new__(ReentrantStub)
            locked = LockedTagger(stub)  # type: ignore[arg-type]
            stub._locked = locked
            locked("outer")
        finally:
            LockedTagger._parse_lock = original_lock  # type: ignore[attr-defined]

        assert calls == ["outer", "inner"], f"Unexpected calls: {calls}"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestRealTagger:
    """Smoke test against a real fugashi.Tagger (skipped if not installed)."""

    def test_real_tagger_is_callable(self):
        """get_shared_tagger() returns a LockedTagger that can parse a short string."""
        tagger = get_shared_tagger()
        assert isinstance(tagger, LockedTagger)
        # calling it tokenises a string
        tokens = tagger("食べる")
        assert tokens is not None
        # Expect at least one token for a verb
        assert len(list(tokens)) >= 1
