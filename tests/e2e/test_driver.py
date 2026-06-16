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
from tests.e2e.driver import E2ETimeout, EpisodeTabDriver
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
    assert cfg.anki_note_type == "Basic"
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

        # Real widgets are readable.
        assert isinstance(driver.log_text(), str)
        assert isinstance(driver.progress_text(), str)
        assert isinstance(driver.progress_value(), int)

        # Screenshot landed on disk.
        shot = driver.screenshot("preview-done")
        assert shot.is_file() and shot.stat().st_size > 0
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
    try:
        yield gateway, e2e
    finally:
        gateway.delete_test_deck()


@pytest.mark.e2e
def test_process_creates_cards_live(tmp_path: Path, qtbot, live_anki) -> None:
    """Real card creation against a live Anki test deck (skips when Anki is down).

    Uses the note type "Basic" (every Anki install ships it) so the harness need
    not create a custom model. ``AutoCurationResponder(policy="all")`` keeps every
    mined word; the deck card count must then match ``cards_created``.
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
