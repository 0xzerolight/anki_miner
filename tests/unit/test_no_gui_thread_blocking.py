"""Static tripwire against reintroduced GUI-thread-blocking calls.

WHY THIS EXISTS
---------------
The ``gui-freeze-hardening`` branch moved a batch of slow, synchronous
operations (ffprobe probes, registry scans + sqlite opens during processor
construction, blocking sqlite stats reads, ASR/CUDA probes, retry-loop rmtrees)
OFF the GUI thread, behind ``anki_miner.gui.utils.run_off_thread.run_off_thread``
or a worker ``QThread``. Run on the GUI thread, each of these freezes the UI —
exactly the "Not responding" class of bug the branch fixed.

This test scans the GUI source for those known-blocking patterns and asserts
that every file still containing one is in a curated, justified :data:`ALLOWLIST`.
The allowlist records the *legitimate* remaining occurrences: a call that now
runs inside a ``run_off_thread`` work callable / a worker thread, a deliberately
cheap probe, or a documented unavoidable cost. Anything NEW (a pattern in a file
not on the allowlist) fails the test with an actionable message.

THE CONTRACT (intended friction — read before "fixing" a failure)
-----------------------------------------------------------------
When this test fails it means a scanned GUI file gained one of the blocking
patterns. EITHER:

* The new call genuinely runs on the GUI thread and is not cheap → it is a
  regression. Move it off the GUI thread via ``run_off_thread`` (or a worker),
  the way ``single_episode_tab._probe`` / ``analytics_tab._fetch`` do. Do NOT
  allowlist it.
* The new call runs off-thread (inside a ``work`` callable / worker) OR is
  genuinely cheap / unavoidable → add ``"<relative/path.py>"`` to that pattern's
  set in :data:`ALLOWLIST` WITH a one-line comment explaining why.

The match key is ``(pattern, file)``, never a line number, so legitimate code
moves inside a file don't churn the allowlist.

SCOPE
-----
Scans ``anki_miner/gui/widgets`` and ``anki_miner/gui/controllers`` recursively.
``anki_miner/gui/workers`` is intentionally EXCLUDED — workers run off the GUI
thread, so blocking there is the whole point. Test files are not scanned.

A note on docstrings/comments: the scan is textual, so a pattern mentioned only
in a docstring or comment (e.g. the many ``_curation_event.wait()`` references)
still counts as a hit for its file. Those files are allowlisted anyway — a
mention in prose cannot block the GUI thread, and excluding comments would make
the scanner brittle. The allowlist comment notes when an entry is prose-only.
Because of this textual matching, several allowlist entries exist only because a
*prose* mention of the pattern inflates the file's hit set — not because of a
live call. Patterns must also be single-line: a regex spanning two source lines
(e.g. registry construction on one line and ``.load()`` on the next) matches
nothing, so such cross-line shapes are deliberately not patterns here.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root: tests/unit/<this file> -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUI = _REPO_ROOT / "anki_miner" / "gui"
_SCAN_DIRS = (_GUI / "widgets", _GUI / "controllers")

#: GUI-thread-blocking patterns this branch moved off-thread (and a few cheap
#: ones kept deliberately). Each is a regex matched against raw file text.
_BLOCKING_PATTERNS: tuple[str, ...] = (
    r"subprocess\.",
    r"\brequests\.(get|post|Session)",
    r"\burllib\b",
    r"shutil\.which\(",
    r"\.wait\(\s*\)",  # untimed QThread.wait(); bounded .wait(ms) is fine
    r"time\.sleep\(",
    r"\.processEvents\(",
    r"get_overall_stats\(",
    # NOTE: a single-line ``Registry(...).load()`` pattern was removed — registry
    # construction and ``.load()`` are always on separate source lines, so it
    # matched nothing (dead pattern giving false confidence). The expensive
    # registry scans it aimed at are already covered: every GUI ``.load()`` runs
    # inside a processor_factory / settings-panel work callable dispatched via
    # run_off_thread (see create_episode_processor / time.sleep allowlists).
    r"create_episode_processor\(",
    r"parse_raw_entries\(",
    r"list_audio_streams\(",
    r"get_primary_video_codec\(",
    r"find_japanese_audio_stream\(",
    r"cuda_device_count\(",
    r"is_downloaded\(",
    r"\.rglob\(",
)

#: pattern -> {relative file paths where the pattern legitimately remains}.
#:
#: Every entry was audited against the source on this branch. Each occurrence is
#: either (a) inside a ``run_off_thread`` ``work`` callable / a worker thread,
#: (b) a genuinely cheap probe, (c) a documented unavoidable GUI-thread cost, or
#: (d) prose only (docstring/comment). The justification is on each entry.
#:
#: Paths are relative to ``anki_miner/gui``.
ALLOWLIST: dict[str, set[str]] = {
    # requests.* — ALL occurrences are docstring/comment prose describing the
    # processor's owned requests.Session (teardown ordering notes). No live call.
    r"\brequests\.(get|post|Session)": {
        "widgets/youtube_tab.py",
        "widgets/_mining_tab_base.py",
        "widgets/single_episode_tab.py",
        "widgets/audiobook_tab.py",
        # Prose only: update_config's docstring notes the processor owns a
        # requests.Session (lazy-drop teardown ordering). No live call.
        "widgets/_reading_mining_base.py",
    },
    # urllib — only `from urllib.parse import urlparse`: pure string parsing of a
    # URL, no network I/O. Cheap.
    r"\burllib\b": {
        "widgets/youtube_playlist_flow.py",
    },
    # shutil.which — a single cheap PATH scan to test for the `alass` binary,
    # cached on the widget (`_alass_is_available`); readers use the cache.
    r"shutil\.which\(": {
        "widgets/subtitle_retime_tab.py",
    },
    # untimed .wait() — each is a legitimate join/event-wait, not a GUI freeze:
    r"\.wait\(\s*\)": {
        # _curation_event.wait(): blocks the WORKER thread until the GUI sets the
        # curation result (the worker->GUI->worker handoff). Worker-side, not GUI.
        # Most hits in these files are docstring references to that same wait.
        "widgets/_mining_tab_base.py",
        "widgets/youtube_tab.py",
        "widgets/batch_processing_tab.py",
        "widgets/audiobook_tab.py",
        # Prose only: shutdown's docstring references _curation_event.wait() (the
        # worker-side park the poison releases). The real join is the bounded
        # worker_thread.wait(_SHUTDOWN_WAIT_MS), which has an arg and never matches.
        "widgets/_reading_mining_base.py",
        # worker.wait() after loop.exec() already returned on worker.finished —
        # the QThread is finished, so the join returns immediately (cheap).
        "widgets/dialogs/resource_download_dialog.py",
        # Prose only: a comment referencing resource_download_dialog's .wait().
        "widgets/dialogs/setup_wizard/setup_wizard.py",
        # background_tasks._join_worker_for_close: the timeout_ms=None branch is a
        # deliberate unbounded join reserved for SHORT workers with no cancel hook
        # (cache prewarm), on the shutdown/close path. Documented in its docstring.
        "controllers/background_tasks.py",
    },
    # time.sleep — only inside `_robust_rmtree`'s Windows-retry loop, and that
    # helper is only ever called from a `_delete` work callable dispatched via
    # run_off_thread (settings-panel "remove" actions). Off-thread.
    r"time\.sleep\(": {
        "widgets/panels/audio_pack_settings_panel.py",
        "widgets/panels/dictionary_settings_panel.py",
        "widgets/panels/frequency_settings_panel.py",
    },
    # processEvents — a single deliberate pump in ui_settings_panel so the busy
    # cursor paints before an UNAVOIDABLE synchronous repolish of the whole widget
    # tree (documented in the slot: the restyle cost can't be moved off the GUI thread).
    r"\.processEvents\(": {
        "widgets/panels/ui_settings_panel.py",
    },
    # get_overall_stats — inside analytics_tab._fetch, dispatched via run_off_thread.
    r"get_overall_stats\(": {
        "widgets/analytics_tab.py",
    },
    # create_episode_processor — every call is inside a `processor_factory` /
    # `_processor_factory` callable passed to a worker thread, so the slow
    # registry scan + sqlite opens + CSV parses run off the GUI thread (the exact
    # fix this branch made; see single_episode_tab's factory comment).
    r"create_episode_processor\(": {
        "widgets/single_episode_tab.py",
        "widgets/youtube_tab.py",
        "widgets/batch_processing_tab.py",
        "widgets/audiobook_tab.py",
        # _launch_run's lazy-rebuild call is inside a `processor_factory` closure
        # passed to the worker thread, so the registry/sqlite/CSV work runs off
        # the GUI thread (same fix as the other reading/mining tabs).
        "widgets/_reading_mining_base.py",
    },
    # parse_raw_entries — inside single_episode_tab._parse (run_off_thread work
    # callable) and _mining_tab_base's timing-preview parse helper, both off-thread.
    r"parse_raw_entries\(": {
        "widgets/_mining_tab_base.py",
        "widgets/single_episode_tab.py",
    },
    # list_audio_streams — both call it inside their `_probe`/`_on_tracks_clicked`
    # work callables dispatched via run_off_thread. Off-thread. The call text
    # still appears in each file, so both stay allowlisted.
    r"list_audio_streams\(": {
        "widgets/single_episode_tab.py",
        "widgets/subtitle_retime_tab.py",
    },
    # get_primary_video_codec / find_japanese_audio_stream — both inside
    # subtitle_player_widget._probe (run_off_thread). Off-thread.
    r"get_primary_video_codec\(": {
        "widgets/subtitle_player_widget.py",
    },
    r"find_japanese_audio_stream\(": {
        "widgets/subtitle_player_widget.py",
    },
    # cuda_device_count / is_downloaded — inside subtitles_settings_panel._probe
    # (run_off_thread). is_downloaded also appears in subtitle_creation_tab; see
    # below.
    r"cuda_device_count\(": {
        "widgets/panels/subtitles_settings_panel.py",
    },
    r"is_downloaded\(": {
        # _probe work callable (run_off_thread) — off-thread.
        "widgets/panels/subtitles_settings_panel.py",
        # subtitle_creation_tab: a cheap on-disk existence check (model dir +
        # marker) guarding whether ASR can start; not a network/ffprobe call.
        "widgets/subtitle_creation_tab.py",
    },
}


def _relative_gui_path(path: Path) -> str:
    """Return ``path`` relative to ``anki_miner/gui`` with forward slashes."""
    return path.relative_to(_GUI).as_posix()


def _scan_files() -> list[Path]:
    """All ``*.py`` files under the scanned dirs (workers excluded by scope)."""
    files: list[Path] = []
    for root in _SCAN_DIRS:
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _files_matching(pattern: str) -> set[str]:
    """Relative paths of scanned files whose text matches ``pattern``."""
    rx = re.compile(pattern)
    hits: set[str] = set()
    for path in _scan_files():
        text = path.read_text(encoding="utf-8")
        if rx.search(text):
            hits.add(_relative_gui_path(path))
    return hits


def test_scan_dirs_exist_and_are_nonempty():
    """Guard the scanner itself: the dirs exist and contain Python files."""
    for root in _SCAN_DIRS:
        assert root.is_dir(), f"scan dir missing: {root}"
    assert _scan_files(), "no GUI files found to scan — scanner is misconfigured"


def test_no_new_gui_thread_blockers():
    """Every file matching a blocking pattern must be on the curated ALLOWLIST.

    A failure means a scanned GUI file gained a known GUI-thread-blocking call
    outside the allowed set. See this module's docstring for the contract.
    """
    new_blockers: list[str] = []
    for pattern in _BLOCKING_PATTERNS:
        allowed = ALLOWLIST.get(pattern, set())
        for rel in sorted(_files_matching(pattern) - allowed):
            new_blockers.append(
                f"NEW potential GUI-thread blocker: pattern {pattern!r} in {rel}. "
                f"Move it off the GUI thread via run_off_thread, or if it genuinely "
                f"runs off-thread / is cheap, add {rel!r} to ALLOWLIST[{pattern!r}] in "
                f"this test with a justification comment."
            )
    assert not new_blockers, "\n".join(new_blockers)


def test_allowlist_has_no_stale_entries():
    """Allowlisted files must still match — drop entries that no longer apply.

    Keeps the allowlist honest: if a refactor removes the blocking call, its
    allowlist entry should go too (otherwise the allowlist silently rots and
    could mask a later reintroduction in that file).
    """
    stale: list[str] = []
    for pattern, files in ALLOWLIST.items():
        actual = _files_matching(pattern)
        for rel in sorted(files - actual):
            stale.append(
                f"STALE ALLOWLIST entry: pattern {pattern!r} no longer matches {rel} — "
                f"remove it from ALLOWLIST[{pattern!r}]."
            )
    assert not stale, "\n".join(stale)


def test_every_allowlist_pattern_is_a_known_pattern():
    """No allowlist key may reference a pattern that isn't actually scanned."""
    unknown = set(ALLOWLIST) - set(_BLOCKING_PATTERNS)
    assert not unknown, f"ALLOWLIST references patterns not in _BLOCKING_PATTERNS: {sorted(unknown)}"
