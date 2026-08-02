"""Contract tests for the promoted UI atlas harness (``scripts/ui_atlas``).

The harness previously lived under ``docs/``, which is gitignored: it existed in
exactly one person's checkout, no worktree could run it, nothing noticed when the
app moved out from under it, and by the time it was promoted several of its patch
targets and object names had drifted. These tests are the reason that cannot
happen again. They pin the four things the harness silently depends on:

* the object names it looks for are the ones the widgets actually set;
* the patch targets it neutralises still exist at the bindings it patches
  (an ``unittest.mock.patch`` of a name a module no longer imports looks
  installed and does nothing — that is how the destructive startup GC was nearly
  left live);
* the screen list is the app's registry, so a new screen is covered by
  construction;
* the new D6/D10 checkers actually fire on the defect they exist for, and stay
  quiet on a clean widget.

The driver modules (``atlas``/``sweeps``/``timeline``) call
``isolation.bootstrap()`` at import, which rewrites ``ANKI_MINER_HOME`` for the
whole process — so this file imports only the side-effect-free modules
(``cells``, ``probe``, ``isolation``), loaded by path under private names so
nothing generic lands in ``sys.modules``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QScrollArea, QTabWidget, QVBoxLayout, QWidget

HARNESS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ui_atlas"


def _load(name: str):
    """Import a harness module by path, under a private name."""
    spec = importlib.util.spec_from_file_location(f"_ui_atlas_{name}", HARNESS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load("probe")


@pytest.fixture(scope="module")
def cells():
    return _load("cells")


@pytest.fixture(scope="module")
def isolation():
    return _load("isolation")


class TestHarnessIsTracked:
    def test_every_harness_module_exists(self):
        """The whole point of the promotion: these files are in the repo now."""
        for name in ("isolation.py", "probe.py", "cells.py", "atlas.py", "sweeps.py", "timeline.py"):
            assert (HARNESS_DIR / name).is_file(), f"{name} is missing from scripts/ui_atlas"

    def test_the_repo_root_is_derived_not_hard_coded(self, isolation):
        """The 2026-07-25 original pinned one audit worktree by absolute path."""
        assert (isolation.REPO / "anki_miner" / "gui" / "app.py").is_file()

    def test_the_scratch_home_is_never_the_real_home(self, isolation):
        assert isolation.SCRATCH_HOME != isolation.REAL_HOME


class TestObjectNamesTheCheckersKeyOn:
    def test_the_pinned_bar_name_matches_the_widget(self, probe):
        from anki_miner.gui.widgets.base.workflow_action_bar import ACTION_BAR_OBJECT_NAME

        assert probe.ACTION_BAR_OBJECT_NAME == ACTION_BAR_OBJECT_NAME

    def test_a_primary_button_carries_the_name_the_checker_looks_for(self, probe, qtbot):
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        button = ModernButton("Run", variant="primary")
        qtbot.addWidget(button)

        assert button.objectName() == probe.PRIMARY_OBJECT_NAME


class TestPatchTargetsStillExist:
    """A patch of a name a module no longer binds is inert but looks installed."""

    def test_app_still_binds_the_destructive_startup_recovery(self):
        import anki_miner.gui.app as app_mod

        assert hasattr(app_mod, "run_startup_store_recovery")

    def test_app_still_binds_the_translator_installer(self):
        import anki_miner.gui.app as app_mod

        assert hasattr(app_mod, "install_translators")

    def test_the_subtitle_player_still_binds_the_gl_surface(self):
        import anki_miner.gui.widgets.subtitle_player_widget as spw

        assert hasattr(spw, "MpvVideoWidget")

    def test_the_file_dialog_wrappers_are_all_present(self):
        import anki_miner.gui.utils.file_dialogs as fd

        for name in ("pick_open_file", "pick_open_files", "pick_save_file", "pick_directory"):
            assert hasattr(fd, name), f"file_dialogs.{name} is gone; isolation.patched_modals is now partly inert"
        assert hasattr(fd, "cancel_all_pickers"), "isolation.patched_modals calls this on teardown"
        # A half-finished rename would leave the old blocking wrappers behind
        # and the atlas would patch the wrong four names.
        for gone in ("get_open_file_name", "get_open_file_names", "get_save_file_name", "get_existing_directory"):
            assert not hasattr(fd, gone), f"file_dialogs.{gone} is back; the blocking wrappers must stay deleted"

    def test_validation_start_is_still_suppressible(self):
        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        assert hasattr(BackgroundTaskController, "start_validation")


class TestTheCellTable:
    def test_the_two_cells_the_plan_names_are_exact(self, cells):
        assert cells.CELLS["reference"][:5] == (1280, 800, 1.0, "en", False)
        assert cells.CELLS["hostile"][:5] == (1024, 768, 1.5, "de", False)

    def test_the_screen_list_is_the_capability_registry(self, cells):
        """A new tab or sub-tab joins the atlas without editing the harness."""
        from anki_miner.gui.capabilities import MAIN_TABS, SUBTAB_KEYS

        expected = set()
        for main in MAIN_TABS:
            subs = SUBTAB_KEYS.get(main)
            if subs:
                expected.update(f"{main}.{sub}" for sub in subs)
            else:
                expected.add(main)

        assert {label for label, _, _ in cells.screens_to_visit()} == expected


class TestTheNewCheckersAreNotVacuous:
    """D6 and D10 oracles: they must fire on the defect and stay quiet without it."""

    def test_a_primary_below_the_window_edge_is_reported(self, probe, qtbot):
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        root = QWidget()
        qtbot.addWidget(root)
        root.resize(400, 120)
        scroll = QScrollArea(root)
        scroll.setGeometry(0, 0, 400, 120)
        page = QWidget()
        layout = QVBoxLayout(page)
        for _ in range(12):
            layout.addWidget(QLabel("filler"))
        layout.addWidget(ModernButton("Run", variant="primary"))
        scroll.setWidget(page)
        root.show()
        qtbot.waitExposed(root)

        findings = probe.check_10_primary_action_hidden(root, "synthetic")

        assert findings, "a run button pushed below the fold was not reported"
        assert findings[0]["severity"] == "below_fold"

    def test_an_on_screen_primary_is_not_reported(self, probe, qtbot):
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        root = QWidget()
        qtbot.addWidget(root)
        root.resize(400, 200)
        layout = QVBoxLayout(root)
        layout.addWidget(ModernButton("Run", variant="primary"))
        root.show()
        qtbot.waitExposed(root)

        assert probe.check_10_primary_action_hidden(root, "synthetic") == []

    def test_an_overflowing_tab_strip_is_reported(self, probe, qtbot):
        root = QWidget()
        qtbot.addWidget(root)
        root.resize(200, 120)
        layout = QVBoxLayout(root)
        tabs = QTabWidget()
        for index in range(10):
            tabs.addTab(QWidget(), f"A rather long tab label {index}")
        layout.addWidget(tabs)
        root.show()
        qtbot.waitExposed(root)

        findings = probe.check_11_tabbar_overflow(root, "synthetic")

        assert findings, "a tab strip that does not fit was not reported"
        assert findings[0]["needed_px"] > findings[0]["available_px"]

    def test_a_fitting_tab_strip_is_not_reported(self, probe, qtbot):
        root = QWidget()
        qtbot.addWidget(root)
        root.resize(600, 200)
        layout = QVBoxLayout(root)
        tabs = QTabWidget()
        tabs.addTab(QWidget(), "One")
        tabs.addTab(QWidget(), "Two")
        layout.addWidget(tabs)
        root.show()
        qtbot.waitExposed(root)

        assert probe.check_11_tabbar_overflow(root, "synthetic") == []

    def test_the_settings_navigator_is_not_a_tab_strip(self, probe, qtbot, test_config):
        """D10: Settings must not be able to overflow, because it has no strip."""
        from PyQt6.QtWidgets import QListWidget, QTabBar

        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        assert isinstance(tab.nav_list, QListWidget)
        assert tab.findChildren(QTabBar) == []

    def test_every_checker_survives_an_empty_widget(self, probe, qtbot):
        """A checker that raises would silently disappear from the atlas."""
        root = QWidget()
        qtbot.addWidget(root)
        root.resize(300, 200)
        root.show()
        qtbot.waitExposed(root)

        findings = probe.run_checkers(root, "empty")

        assert [f for f in findings if "error" in f] == []
