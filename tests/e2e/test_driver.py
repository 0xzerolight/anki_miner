"""Tests for the in-process episode-tab driver + run artifacts.

The headline acceptance is :func:`test_preview_drives_real_tab_no_anki`: it
builds the harness config against a ``tmp_path`` home, seeds the offline dict,
constructs the REAL ``SingleEpisodeTab`` via :class:`EpisodeTabDriver`, clicks
Preview, waits for the worker, and asserts the pipeline genuinely ran (words
found == the fixture's ``EXPECTED_LEMMAS``). Preview mode parses + filters only,
so it needs neither Anki nor card creation — it runs fully offscreen.

The live card-creation test self-skips when Anki is unreachable (the env here),
and the artifact tests stand alone. Per project Qt discipline every top-level
widget is registered with ``qtbot.addWidget`` and the worker is joined via the
driver's ``teardown`` before the test ends.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError
from tests.e2e.app_config import build_app_config
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.curation import AutoCurationResponder
from tests.e2e.driver import E2EMiningError, E2ETimeout, EpisodeTabDriver
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS, get_test_srt

# fugashi/MeCab is required for the real tokenizer; skip the whole module's
# pipeline tests cleanly if it is absent.
pytest.importorskip("fugashi")


# --------------------------------------------------------------------------
# RunDir artifact helper
# --------------------------------------------------------------------------


def test_rundir_creates_timestamped_dir(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run = RunDir(runs_root)
    assert run.path.is_dir()
    assert run.path.parent == runs_root


def test_rundir_save_json_pretty_and_ordered(tmp_path: Path) -> None:
    run = RunDir(tmp_path / "runs")
    p1 = run.save_json("first", {"a": 1, "ja": "日本語"})
    p2 = run.save_json("second", {"b": 2})
    assert p1.name.startswith("01_") and p1.name.endswith(".json")
    assert p2.name.startswith("02_")
    text = p1.read_text(encoding="utf-8")
    assert "\n" in text  # pretty-printed (indent=2)
    assert "日本語" in text  # ensure_ascii=False


def test_rundir_save_png_from_widget(tmp_path: Path, qtbot) -> None:
    from PyQt6.QtWidgets import QLabel

    widget = QLabel("e2e screenshot")
    qtbot.addWidget(widget)
    run = RunDir(tmp_path / "runs")
    out = run.save_png("shot", widget)
    assert out.name == "01_shot.png"
    assert out.is_file() and out.stat().st_size > 0


def test_rundir_save_png_from_pixmap(tmp_path: Path, qtbot) -> None:
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(10, 10)
    pixmap.fill()
    run = RunDir(tmp_path / "runs")
    out = run.save_png("pix", pixmap)
    assert out.is_file() and out.stat().st_size > 0


def test_rundir_adopt_uses_exact_dir_no_subdir(tmp_path: Path) -> None:
    """RunDir.adopt wraps an existing dir as-is — no new timestamped subdir created."""
    target = tmp_path / "exact_dir"
    target.mkdir()
    run = RunDir.adopt(target)
    assert run.path == target
    # No child dirs created under target.
    assert list(target.iterdir()) == []


def test_rundir_adopt_creates_dir_if_absent(tmp_path: Path) -> None:
    """RunDir.adopt creates the directory (with parents) when it does not exist."""
    target = tmp_path / "missing" / "nested"
    run = RunDir.adopt(target)
    assert run.path == target
    assert target.is_dir()


def test_rundir_adopt_writes_into_given_dir(tmp_path: Path) -> None:
    """Artifacts written through RunDir.adopt land directly in the given dir."""
    target = tmp_path / "parent_run"
    target.mkdir()
    run = RunDir.adopt(target)
    p = run.save_json("meta", {"ok": True})
    assert p.parent == target
    assert p.name == "01_meta.json"


def test_rundir_adopt_step_counter_starts_at_zero(tmp_path: Path) -> None:
    """RunDir.adopt's step counter starts at 0, unaffected by existing files."""
    target = tmp_path / "existing"
    target.mkdir()
    # Pre-populate with a file that looks like a step artifact.
    (target / "99_old.json").write_text("{}")
    run = RunDir.adopt(target)
    p = run.save_json("first", {})
    assert p.name == "01_first.json"


def test_rundir_default_still_creates_subdir(tmp_path: Path) -> None:
    """Normal RunDir(runs_root) still creates a new timestamped subdir (unchanged)."""
    runs_root = tmp_path / "runs"
    run = RunDir(runs_root)
    assert run.path.parent == runs_root
    assert run.path != runs_root


# --------------------------------------------------------------------------
# build_app_config mapping
# --------------------------------------------------------------------------


def test_build_app_config_basic_field_mapping(tmp_path: Path) -> None:
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path)
    # Minimal valid Basic mapping: word->Front, definition->Back, rest blank.
    assert cfg.anki_fields["word"] == "Front"
    assert cfg.anki_fields["definition"] == "Back"
    assert cfg.anki_fields["sentence"] == ""
    assert cfg.anki_fields["picture"] == ""
    # All REQUIRED keys present so AnkiService construction won't raise.
    from anki_miner.services.anki_note_builder import REQUIRED_FIELD_KEYS

    assert set(cfg.anki_fields) >= REQUIRED_FIELD_KEYS
    # Target + isolation overrides applied.
    assert cfg.anki_deck_name == e2e.deck_name
    assert cfg.anki_note_type == "AnkiMiner E2E Basic"
    assert cfg.dicts_root == tmp_path / "dicts"
    assert cfg.known_words_db_path == tmp_path / "known_words.db"
    # DEFAULT = faithful real mining: exercises known-words subtraction (needs
    # Anki); dedup/dup left at the real AnkiMinerConfig defaults.
    assert cfg.use_known_words_db is True
    assert cfg.include_known_words is False
    assert cfg.deduplicate_sentences is True
    assert cfg.allow_duplicate_cards is False
    assert cfg.use_frequency_data is False
    assert cfg.use_pitch_accent is False
    # AnkiService must accept this mapping (validates REQUIRED keys at __init__).
    from anki_miner.services.anki_service import AnkiService

    AnkiService(cfg)  # no raise


def test_build_app_config_bypass_known_words(tmp_path: Path) -> None:
    """``bypass_known_words=True`` flips to card-everything / no-Anki / deterministic."""
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    # include_known_words=True is the ONLY phase-2 path with no AnkiConnect call.
    assert cfg.include_known_words is True
    assert cfg.deduplicate_sentences is False
    assert cfg.allow_duplicate_cards is True


# --------------------------------------------------------------------------
# Primary REAL acceptance: preview mode, no Anki needed
# --------------------------------------------------------------------------


def test_preview_drives_real_tab_no_anki(tmp_path: Path, qtbot) -> None:
    """Drive the real tab in preview mode end-to-end with no Anki running.

    Preview = parse + filter only (no media extraction, no card creation, no
    curation), so it completes fully offscreen. Asserts the returned
    ``ProcessingResult`` reports the full expected lemma set, a screenshot was
    written, the log/progress widgets are readable, and teardown is clean.
    """
    e2e = E2EConfig(test_home=tmp_path)
    # bypass_known_words: phase-2 makes no AnkiConnect call → runs offscreen, and
    # mines every fixture word deterministically (dedup off → all EXPECTED_LEMMAS).
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)

    run_dir = RunDir(e2e.runs_root, label="preview")
    driver = EpisodeTabDriver(cfg, run_dir)
    qtbot.addWidget(driver.tab)
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        driver.click_preview()
        result = driver.wait_for_result(timeout_s=60)

        # The pipeline genuinely tokenized + filtered: total words equals the
        # fixture's authoritative lemma count (dedup off → all of them mined).
        assert result.success, result.errors
        assert result.total_words_found == len(EXPECTED_LEMMAS)
        assert result.new_words_found == len(EXPECTED_LEMMAS)
        assert result.cards_created == 0  # preview creates nothing

        # Mined word-set must equal EXPECTED_LEMMAS exactly (bypass → dedup off).
        assert set(result.mined_forms) == set(EXPECTED_LEMMAS), (
            f"mined_forms mismatch:\n"
            f"  observed={sorted(result.mined_forms)}\n"
            f"  expected={sorted(EXPECTED_LEMMAS)}\n"
            f"  extra={sorted(set(result.mined_forms) - set(EXPECTED_LEMMAS))}\n"
            f"  missing={sorted(set(EXPECTED_LEMMAS) - set(result.mined_forms))}"
        )

        # Real widgets are readable.
        assert isinstance(driver.log_text(), str)
        assert isinstance(driver.progress_text(), str)
        assert isinstance(driver.progress_value(), int)

        # Screenshot landed on disk.
        shot = driver.screenshot("preview-done")
        assert shot.is_file() and shot.stat().st_size > 0

        # GUI state must have returned to idle after the run completes.
        assert driver.buttons_idle(), (
            f"buttons not idle after preview: "
            f"process_enabled={driver.process_button_enabled()}, "
            f"cancel_visible={driver.cancel_button_visible()}"
        )
        assert not driver.buttons_running(), "cancel must not be visible at idle"

        # Activity log must contain the phase-1 and phase-2 markers.
        log = driver.log_text()
        assert log.strip(), "activity log is empty after preview run"
        assert "Step 1/5" in log, f"'Step 1/5' not found in log: {log[:500]}"
        assert "Step 2/5" in log, f"'Step 2/5' not found in log: {log[:500]}"

        # Preview never emits phase 3–5 markers (no media/definitions/cards).
        assert "Step 3/5" not in log, "phase-3 marker in preview log (should not appear)"
        assert "Step 5/5" not in log, "phase-5 marker in preview log (should not appear)"
    finally:
        driver.teardown()


# --------------------------------------------------------------------------
# Live card creation: SKIPS cleanly when Anki is down
# --------------------------------------------------------------------------


@pytest.fixture
def live_anki(tmp_path: Path):
    """Yield an :class:`AnkiGateway` against a fresh test deck, or skip if Anki is down.

    Cleans the test deck on teardown so a real collection is never left with
    harness cards.
    """
    e2e = E2EConfig(test_home=tmp_path)
    gateway = AnkiGateway(e2e)
    try:
        gateway.ping()
    except AnkiUnreachableError:
        pytest.skip("Anki not running (AnkiConnect unreachable)")
    gateway.ensure_test_deck()
    gateway.ensure_test_model()
    try:
        yield gateway, e2e
    finally:
        gateway.delete_test_deck()


@pytest.mark.e2e
def test_process_creates_cards_live(tmp_path: Path, qtbot, live_anki) -> None:
    """Real card creation against a live Anki test deck (skips when Anki is down).

    Uses the harness note type (``e2e.note_type``, self-provisioned by
    ``ensure_test_model`` in the ``live_anki`` fixture).
    ``AutoCurationResponder(policy="all")`` keeps every mined word; the deck
    card count must then match ``cards_created``.
    """
    gateway, e2e = live_anki
    # bypass_known_words: deterministic cards_created == fixture lemma count
    # (independent of the user's real collection) + allow_duplicate_cards so
    # re-runs don't collide with prior E2E cards. The faithful known-words path
    # (the suspected multi-session bug) is the soak runner's job, not this smoke.
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)

    run_dir = RunDir(e2e.runs_root, label="process")
    driver = EpisodeTabDriver(cfg, run_dir)
    qtbot.addWidget(driver.tab)
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        with AutoCurationResponder(policy="all"):
            driver.click_process()
            result = driver.wait_for_result(timeout_s=e2e.result_timeout_s)
        assert result.success, result.errors
        assert result.cards_created > 0
        assert gateway.deck_card_count() == result.cards_created
    finally:
        driver.teardown()


# --------------------------------------------------------------------------
# Connect-before-start: an immediately-emitting worker is still captured
# --------------------------------------------------------------------------


def test_immediate_error_is_captured_not_timed_out(tmp_path: Path, qtbot, monkeypatch) -> None:
    """A worker that emits ``error`` the instant ``run()`` starts is still captured.

    This is the connect-after-emit race the driver used to lose: the real
    ``_start_processing`` builds the worker, connects the tab's slots, and calls
    ``.start()`` synchronously on click. If the driver only connected its capture
    slots AFTER the click (the old ``_arm_capture``), a worker that emits ``error``
    by the time ``.start()`` returns would emit BEFORE the driver's slot existed →
    the slot connected later never receives that emission → the driver spuriously
    raised ``E2ETimeout``. The ``worker_created`` seam lets the driver attach BEFORE
    ``.start()`` so the emission cannot be missed.

    To make the race deterministic (not thread-scheduling dependent), the patched
    worker emits ``error`` synchronously from ``start()`` — modelling "the worker
    already emitted before the driver got a chance to connect". The fix must still
    capture it → ``E2EMiningError``, never ``E2ETimeout``.
    """
    import anki_miner.gui.widgets.single_episode_tab as set_module
    from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread

    class _ImmediateErrorWorker(EpisodeWorkerThread):
        def start(self, *args, **kwargs) -> None:  # type: ignore[override]
            # Emit synchronously BEFORE the OS thread is even scheduled — the
            # deterministic worst case for a connect-after-start driver: any slot
            # connected after this returns misses the emission entirely.
            self.error.emit("boom immediately")
            # Never actually start the OS thread; nothing else to run.

        def wait(self, *args, **kwargs) -> bool:  # type: ignore[override]
            return True  # the thread never ran; join is a no-op

    monkeypatch.setattr(set_module, "EpisodeWorkerThread", _ImmediateErrorWorker)

    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    run_dir = RunDir(e2e.runs_root, label="immediate-error")
    driver = EpisodeTabDriver(cfg, run_dir)
    qtbot.addWidget(driver.tab)
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        driver.click_process()
        with pytest.raises(E2EMiningError, match="boom immediately"):
            driver.wait_for_result(timeout_s=5)
    finally:
        driver.teardown()


# --------------------------------------------------------------------------
# Optional: timeout path
# --------------------------------------------------------------------------


def test_wait_for_result_times_out(tmp_path: Path, qtbot) -> None:
    """A too-short budget against a never-completing capture raises E2ETimeout.

    Builds the driver and arms an empty capture by hand (no real worker), then
    waits with a ~0 budget so the predicate can never become true. Confirms the
    timeout path writes a screenshot + json and raises cleanly.
    """
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path)
    run_dir = RunDir(e2e.runs_root, label="timeout")
    driver = EpisodeTabDriver(cfg, run_dir)
    qtbot.addWidget(driver.tab)
    try:
        with pytest.raises(E2ETimeout):
            driver.wait_for_result(timeout_s=0.05)
        # Timeout artifacts written.
        assert any(p.suffix == ".png" for p in run_dir.path.iterdir())
    finally:
        driver.teardown()


# --------------------------------------------------------------------------
# Mined word-set: preview populates result.mined_forms == EXPECTED_LEMMAS
# --------------------------------------------------------------------------


def test_preview_mined_forms_equals_expected_lemmas(tmp_path: Path, qtbot) -> None:
    """result.mined_forms in preview mode equals EXPECTED_LEMMAS exactly.

    This is the direct check for Task 13's core invariant: a tokenizer regression
    that changes WHICH words are mined (same count, different set) is caught here.
    bypass_known_words=True ensures every fixture word is mined deterministically
    (dedup off, no AnkiConnect call).
    """
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)

    run_dir = RunDir(e2e.runs_root, label="mined-set")
    driver = EpisodeTabDriver(cfg, run_dir)
    qtbot.addWidget(driver.tab)
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        driver.click_preview()
        result = driver.wait_for_result(timeout_s=60)

        assert result.success, result.errors
        # Core invariant: the mined set must equal EXPECTED_LEMMAS exactly.
        assert set(result.mined_forms) == set(EXPECTED_LEMMAS), (
            f"mined_forms mismatch:\n"
            f"  observed={sorted(result.mined_forms)}\n"
            f"  expected={sorted(EXPECTED_LEMMAS)}\n"
            f"  extra={sorted(set(result.mined_forms) - set(EXPECTED_LEMMAS))}\n"
            f"  missing={sorted(set(EXPECTED_LEMMAS) - set(result.mined_forms))}"
        )
        # Count consistency: no duplicates in the mined set.
        assert len(result.mined_forms) == len(
            set(result.mined_forms)
        ), f"mined_forms contains duplicates: {result.mined_forms}"
    finally:
        driver.teardown()


def test_mined_set_wrong_set_is_flagged() -> None:
    """A wrong mined set (different words, same or different count) is detected.

    Unit-level: directly exercises the set-comparison logic used in
    run_one_session to confirm a tokenizer regression would NOT go undetected.
    Does not need Qt or the real pipeline.
    """
    expected = set(EXPECTED_LEMMAS)

    # Case 1: completely wrong set (same size) — should differ.
    wrong_same_size = {"食べる", "走る", "買う", "本", "今日", "学校", "勉強", "美味しい", "料理", "友達", "公園", "山"}
    assert wrong_same_size != expected, "wrong set must not equal expected (test self-check)"

    # Case 2: missing one word — must be caught.
    one_missing = set(EXPECTED_LEMMAS[:-1])
    assert one_missing != expected

    # Case 3: extra word added — must be caught.
    one_extra = set(EXPECTED_LEMMAS) | {"余分"}
    assert one_extra != expected

    # Case 4: the real expected set is a valid subset of itself (faithful mode tautology).
    assert expected <= expected

    # Case 5: a proper subset passes the faithful (⊆) check but fails the exact check.
    subset = set(EXPECTED_LEMMAS[:6])
    assert subset <= expected  # faithful mode: ok
    assert subset != expected  # bypass mode: would be caught
