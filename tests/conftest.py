"""Pytest configuration and shared fixtures."""

import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.presenters import NullPresenter, NullProgressCallback

# Single source of truth for the home-isolation mechanism, shared with the
# standalone (non-pytest) E2E runner which never sees this conftest. See
# tests/_home_isolation.py for the full WHY (independent per-module
# ``ANKI_MINER_HOME`` snapshots, the real-config-clobber bug it fixes).
from tests._home_isolation import (
    apply_home_patches as _apply_home_patches,
)
from tests._home_isolation import (
    restore_home_patches as _restore_home_patches,
)
from tests._home_isolation import (
    snapshot_home as _snapshot_home,
)

# The GENUINE user home, resolved at conftest import time BEFORE any test can
# set ANKI_MINER_HOME. ``_guard_real_home`` watches this exact path so the env
# patching done by ``_isolate_anki_home`` can never fool the tripwire into
# watching the throwaway tmp dir instead of the real one.
_REAL_ANKI_HOME = Path(os.path.expanduser("~")) / ".anki_miner"


@pytest.fixture(autouse=True)
def _no_logger_level_leak():
    """Fail any test that leaves the root or ``anki_miner`` logger level changed.

    ``_configure_logging`` (gui/app.py) pins the ``anki_miner`` namespace logger
    to DEBUG. A test that triggers it and forgets to restore the level pollutes
    every later test sharing the same xdist worker. Leaked DEBUG silently flips
    DEBUG-gated production paths on -- e.g. the subtitle player teardown drain
    (gui/widgets/subtitle_player_widget.py) calls
    ``QCoreApplication.sendPostedEvents`` against a mocked player ONLY under
    DEBUG, which raises ``TypeError`` and crashes the worker. Because
    ``--dist loadfile`` sharding is worker-count dependent, the leak passes
    locally and fails in CI. This guard restores the levels unconditionally (so a
    leak can never cascade) and pins the blame to the offending test instead of a
    random downstream Qt crash three files away.
    """
    root = logging.getLogger()
    am = logging.getLogger("anki_miner")
    before = (root.level, am.level)
    try:
        yield
    finally:
        after = (root.level, am.level)
        # Restore unconditionally so a leak cannot poison the next test even if
        # this fixture's assertion is later relaxed.
        root.setLevel(before[0])
        am.setLevel(before[1])
        leaked = after != before
    # Reached only when the test itself passed -- a genuine test failure
    # propagates past the ``finally`` above, so this never masks one.
    if leaked:
        pytest.fail(
            f"test leaked logger levels (root {before[0]}->{after[0]}, "
            f"anki_miner {before[1]}->{after[1]}); restore them in a finally. "
            "Leaked DEBUG flips DEBUG-gated production paths on and crashes "
            "later tests sharing the xdist worker.",
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _isolate_anki_home_session(tmp_path_factory):
    """Session-wide SAFETY FLOOR: home/CONFIG_FILE never resolve to the real
    ``~/.anki_miner`` for the ENTIRE session — crucially, also BETWEEN tests when the
    per-test fixture below has already torn down.

    THE BUG: a pytest run overwrote the user's real ``gui_config.json`` with test
    values. The data dir is ``config.paths.ANKI_MINER_HOME`` (now env-overridable), but
    dozens of modules ``from ...paths import ANKI_MINER_HOME`` at import time, snapshotting
    it into their OWN namespace. Per-test patching alone is NOT enough: a real
    ``MainWindow`` test leaks a background ``QThread``/queued callback that fires
    asynchronously AFTER the per-test isolation restored ``CONFIG_FILE`` — in that window
    the write hit the real config (observed: a full run clobbered the real file ~50% of
    the time, the other half the callback fired while per-test isolation was still up).

    This session fixture sets up before any test and tears down after all of them, so the
    redirect is in force during those between-test windows. A stray async write then lands
    in the session tmp dir instead of the user's real config. The per-test fixture stacks
    fresh per-test dirs on top of this for inter-test data isolation.

    Skipped under the ``AMH_NO_ISOLATE=1`` escape hatch (used to reproduce the original
    leak in a throwaway ``HOME``).
    """
    session_home = tmp_path_factory.mktemp("anki_home_session") / ".anki_miner"
    session_home.mkdir(parents=True, exist_ok=True)

    env_was_set = "ANKI_MINER_HOME" in os.environ
    env_prev = os.environ.get("ANKI_MINER_HOME")
    saved: list[tuple[object, str, object]] = []
    if os.environ.get("AMH_NO_ISOLATE") != "1":
        os.environ["ANKI_MINER_HOME"] = str(session_home)
        saved = _apply_home_patches(session_home)
    try:
        yield session_home
    finally:
        _restore_home_patches(saved)
        if env_was_set:
            os.environ["ANKI_MINER_HOME"] = env_prev
        else:
            os.environ.pop("ANKI_MINER_HOME", None)


@pytest.fixture(autouse=True)
def _isolate_anki_home(tmp_path_factory):
    """Per-test isolation: each test gets its OWN tmp home so config/db files one test
    writes never leak into another. Stacks on top of ``_isolate_anki_home_session``,
    which provides the real-home SAFETY floor (see its docstring for the leaked-thread
    rationale).

    Redirects ``ANKI_MINER_HOME`` env + every imported home snapshot + class-level
    ``GUIConfigManager.CONFIG_FILE`` to the per-test dir. Restores BY HAND (not via the
    shared ``monkeypatch`` fixture, whose restore order relative to ``_drain_qt_deletes``
    is indeterminate) so the restore runs AFTER ``_drain_qt_deletes``'s post-yield
    ``processEvents()``: this fixture is defined above ``_drain_qt_deletes`` so it sets
    up first and tears down last. (The session floor backstops the gap this still leaves
    between tests.)

    Skipped under ``AMH_NO_ISOLATE=1``.
    """
    # Dedicated tmp dir (NOT the per-test ``tmp_path``) so tests that ``iterdir()``
    # their own ``tmp_path`` don't see our ``.anki_miner`` dir.
    tmp_home = tmp_path_factory.mktemp("anki_home") / ".anki_miner"
    tmp_home.mkdir(parents=True, exist_ok=True)

    env_was_set = "ANKI_MINER_HOME" in os.environ
    env_prev = os.environ.get("ANKI_MINER_HOME")
    saved: list[tuple[object, str, object]] = []
    if os.environ.get("AMH_NO_ISOLATE") != "1":
        os.environ["ANKI_MINER_HOME"] = str(tmp_home)
        saved = _apply_home_patches(tmp_home)
    try:
        yield tmp_home
    finally:
        _restore_home_patches(saved)
        if env_was_set:
            os.environ["ANKI_MINER_HOME"] = env_prev
        else:
            os.environ.pop("ANKI_MINER_HOME", None)


@pytest.fixture(autouse=True)
def _guard_real_home():
    """Tripwire: fail any test that mutates the genuine ``~/.anki_miner``.

    Defense-in-depth behind ``_isolate_anki_home``: with isolation active this should
    ALWAYS pass. It exists to catch a FUTURE regression (a new module that snapshots the
    home path but isn't in ``HOME_CONSUMERS``, say) before it silently clobbers a real
    user's config again.

    It reads ``_REAL_ANKI_HOME`` — captured at conftest import time from
    ``os.path.expanduser`` independent of the env var — so the env patching in
    ``_isolate_anki_home`` cannot redirect the tripwire to the tmp home. Under the
    ``AMH_NO_ISOLATE=1`` escape hatch (where ``HOME`` is itself pointed at a throwaway
    dir to safely reproduce the leak) it instead watches that throwaway home so a caught
    writer surfaces.

    It never creates the dir: absent-before/absent-after is fine.
    """
    if os.environ.get("AMH_NO_ISOLATE") == "1":
        watched = Path(os.path.expanduser("~")) / ".anki_miner"
    else:
        watched = _REAL_ANKI_HOME

    before = _snapshot_home(watched)
    yield
    after = _snapshot_home(watched)

    if before != after:
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        modified = sorted(p for p in (set(before) & set(after)) if before[p] != after[p])
        parts = []
        if added:
            parts.append(f"created: {added}")
        if removed:
            parts.append(f"deleted: {removed}")
        if modified:
            parts.append(f"modified: {modified}")
        pytest.fail(
            f"Test mutated the real anki_miner home {watched}! " + "; ".join(parts) + ". "
            "A module is writing to the user's real data dir — add its home-path "
            "snapshot to HOME_CONSUMERS in tests/_home_isolation.py."
        )


@pytest.fixture(autouse=True)
def _drain_qt_deletes():
    """Flush pending Qt deletions after each test to prevent cross-test leaks.

    A widget torn down via ``deleteLater()`` is only *scheduled* for C++ destruction:
    the actual ``~QObject`` runs when the event loop delivers a ``DeferredDelete`` event,
    which a bare ``processEvents()`` does NOT flush. Without that flush a deleteLater'd
    SettingsTab (and its still-running child ``QTimer``) survives into later tests; a
    subsequent ``processEvents()`` (here, or in a test's ``QTest.qWait``) then delivers a
    queued ``timeout`` signal to that half-freed widget's lambda -> segfault.

    ``sendPostedEvents(None, DeferredDelete)`` drains every queued deletion synchronously,
    destroying the C++ objects (and their child timers) at the test boundary. It is run
    *before* ``processEvents`` so leaked widgets are gone before any other queued event is
    delivered, and does not wall-clock-wait, so it never fires a pending singleShot.
    """
    yield
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture(autouse=True)
def _reset_theme_state():
    """Reset the ``Theme`` class-level singleton to defaults around every test.

    ``Theme`` keeps all state on the class. Tests that call
    ``Theme.initialize(shipped_dir=<tmp>)`` with custom themes lacking the
    built-in ``dark``/``light`` keys never restore it, so the polluted
    ``_themes``/``_shipped_dir_override`` leak into later tests on the same
    xdist ``--dist loadfile`` worker. The victim
    (``test_theme_alternating_rows``) then reads a ``_themes`` without ``dark``:
    ``get_colors("dark")`` falls back to the first theme (#000000) and
    ``get_stylesheet("dark")`` leaves ``${color-*}`` unresolved -> assertion
    fails. The flake only surfaces when scheduling happens to place a polluter
    file first.

    Reset at setup so each test starts pristine regardless of predecessors;
    ``Theme`` then lazily rediscovers the real shipped themes on next use.
    Guarded on the module already being imported so non-GUI tests do not pay a
    forced PyQt import. Defaults mirror the class declarations in
    ``theme.py``; ``_qss_template`` is an immutable shipped resource, left
    cached.
    """
    mod = sys.modules.get("anki_miner.gui.resources.styles.theme")
    if mod is not None:
        theme_cls = mod.Theme
        theme_cls._instance = None
        theme_cls._current_mode = "light"
        theme_cls._favorites = ("light", "dark")
        theme_cls._themes = {}
        theme_cls._user_dir = None
        theme_cls._shipped_dir_override = None
        theme_cls._state_listener = None
        theme_cls._compiled_qss = {}
        theme_cls._font_scale = 1.0
    yield


@pytest.fixture(autouse=True)
def _no_real_ytdlp_autoupdate(monkeypatch):
    """Stop real-MainWindow tests spawning the live yt-dlp self-update thread.

    MainWindow.__init__ schedules a ``singleShot(0)`` that builds a real
    YtdlpUpdateWorker QThread running a blocking ``yt-dlp --version`` subprocess.
    The autouse ``_drain_qt_deletes`` flush fires that queued lambda, then a later
    flush destroys the still-running parented QThread mid-subprocess -> SIGABRT.
    No-op the trigger so no unit test ever spawns it. The worker's own tests drive
    it directly (stub updater + worker.wait()) and are unaffected.
    """
    from anki_miner.gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_maybe_start_ytdlp_update", lambda self: None, raising=False)


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def test_config(temp_dir):
    """Provide a test configuration with temporary paths."""
    return AnkiMinerConfig(
        anki_deck_name="test_deck",
        anki_note_type="test_note_type",
        anki_word_field="word",
        anki_fields={
            "word": "word",
            "sentence": "sentence",
            "definition": "definition",
            "picture": "picture",
            "audio": "audio",
            "expression_furigana": "expression_furigana",
            "expression_reading": "",
            "sentence_furigana": "sentence_furigana",
            "sentence_reading": "",
            "pitch_position": "PitchPosition",
            "pitch_category": "PitchCategory",
            "frequency": "Frequency",
            "source": "",
        },
        media_temp_folder=temp_dir / "temp_media",
        jmdict_path=temp_dir / "JMdict_e",
        subtitle_offset=0.0,
        max_parallel_workers=2,  # Reduced for tests
        stats_db_path=temp_dir / "stats.db",
        # Keep tests off the real ~/.anki_miner: these paths otherwise default
        # under ANKI_MINER_HOME, so point dicts/known-words/history at tmp too.
        dicts_root=temp_dir / "dicts",
        known_words_db_path=temp_dir / "known_words.db",
        history_db_path=temp_dir / "history.db",
    )


@pytest.fixture
def facade_processor(test_config):
    """Real EpisodeProcessor over MagicMock services.

    For GUI-level tests that exercise the processor's dictionary-resource
    facade (``offline_lookup_fn`` / ``release_dictionary_resources``) against
    a mock definition service without standing up real services (T-60).
    """
    from anki_miner.orchestration.episode_processor import EpisodeProcessor

    return EpisodeProcessor(
        config=test_config,
        subtitle_parser=MagicMock(name="SubtitleParser"),
        word_filter=MagicMock(name="WordFilter"),
        media_extractor=MagicMock(name="MediaExtractor"),
        definition_service=MagicMock(name="DefinitionService"),
        anki_service=MagicMock(name="AnkiService"),
        presenter=NullPresenter(),
    )


@pytest.fixture
def null_presenter():
    """Provide a null presenter for testing (no output)."""
    return NullPresenter()


@pytest.fixture
def null_progress():
    """Provide a null progress callback for testing."""
    return NullProgressCallback()


@pytest.fixture
def make_tokenized_word():
    """Factory fixture for creating TokenizedWord instances with sensible defaults."""

    def _make(
        surface="食べる",
        lemma="食べる",
        reading="タベル",
        sentence="日本語を食べる。",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        video_file=None,
        expression_furigana="",
        expression_reading="",
        sentence_furigana="",
        sentence_reading="",
        frequency_rank=None,
        pos=None,
    ):
        return TokenizedWord(
            surface=surface,
            lemma=lemma,
            reading=reading,
            sentence=sentence,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            video_file=video_file,
            expression_furigana=expression_furigana,
            expression_reading=expression_reading,
            sentence_furigana=sentence_furigana,
            sentence_reading=sentence_reading,
            frequency_rank=frequency_rank,
            pos=pos,
        )

    return _make


@pytest.fixture
def make_media_data(tmp_path):
    """Factory fixture for creating MediaData instances with optional real files."""

    def _make(
        screenshot=True,
        audio=True,
        create_files=False,
        prefix="word_1000",
    ):
        ss_path = tmp_path / f"{prefix}.jpg" if screenshot else None
        au_path = tmp_path / f"{prefix}.mp3" if audio else None
        ss_name = f"{prefix}.jpg" if screenshot else None
        au_name = f"{prefix}.mp3" if audio else None

        if create_files:
            if ss_path:
                ss_path.write_bytes(b"\xff\xd8fake-jpeg")
            if au_path:
                au_path.write_bytes(b"\xff\xfbfake-mp3")

        return MediaData(
            screenshot_path=ss_path,
            audio_path=au_path,
            screenshot_filename=ss_name,
            audio_filename=au_name,
        )

    return _make


class RecordingProgress:
    """A real ProgressCallback implementation that records all calls for assertion."""

    def __init__(self):
        self.starts = []
        self.progresses = []
        self.completes = 0
        self.errors = []

    def on_start(self, total: int, description: str) -> None:
        self.starts.append((total, description))

    def on_progress(self, current: int, item_description: str) -> None:
        self.progresses.append((current, item_description))

    def on_complete(self) -> None:
        self.completes += 1

    def on_error(self, item_description: str, error_message: str) -> None:
        self.errors.append((item_description, error_message))


@pytest.fixture
def recording_progress():
    """Provide a progress callback that records all calls for assertion."""
    return RecordingProgress()


@pytest.fixture
def sample_subtitle_content():
    """Provide sample subtitle content for testing."""
    return """[Script Info]
Title: Test Subtitle

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,これは日本語のテストです。
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,私は学生です。
Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,今日は良い天気ですね。
"""


@pytest.fixture
def sample_subtitle_file(temp_dir, sample_subtitle_content):
    """Create a sample subtitle file for testing."""
    subtitle_file = temp_dir / "test.ass"
    subtitle_file.write_text(sample_subtitle_content, encoding="utf-8")
    return subtitle_file
