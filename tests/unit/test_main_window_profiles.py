"""MainWindow / app wiring for named settings profiles.

The load-bearing assertions are about ORDER and REACH of the boot step:

* the reconcile seeds ``GUIConfigManager.ACTIVE_PROFILE_ID`` BEFORE the
  ``last_known_version`` save — a save that runs first writes gui_config.json
  with no profile marker at all, which is exactly what putting the step in
  either existing ``_run_optional_boot_step`` region would do;
* it also runs on the ``suppress_optional`` (installer-smoke) path, which
  hard-asserts on the gui_config.json that same save produces.

The rest pins the signal wiring: the header combo proposes a switch to the
controller, both discovery surfaces (combo sentinel + Settings → UI button)
open the manager through ``exec``, and ``closeEvent`` deliberately writes no
profile file.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox

from anki_miner import __version__
from anki_miner.gui.controllers.profile_controller import ProfileController, SwitchResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.gui.widgets.dialogs import profile_manager_dialog as dialog_module
from anki_miner.gui.widgets.settings_tab import SettingsTab

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_boot(monkeypatch):
    """Silence every boot step except the profile bootstrap under test.

    ``patch_heavy_init`` covers construction; these are the ``commit_boot``
    extras it deliberately leaves alone (migrations, background updates, the
    deferred first-run timers and the "Anki Miner updated" modal).
    """
    from anki_miner.gui import main_window as mw_module

    for name in (
        "_maybe_repair_legacy_frequency_source_name",
        "_maybe_migrate_legacy_pitch",
        "_maybe_migrate_jmdict",
        "_maybe_start_ytdlp_update",
    ):
        monkeypatch.setattr(mw_module.MainWindow, name, lambda self: None)
    monkeypatch.setattr(mw_module.QTimer, "singleShot", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)


def _record_saves(monkeypatch) -> list[tuple[object, str | None]]:
    """Record ``(config, active-profile marker)`` for every save.

    Applied AFTER ``patch_heavy_init`` (which stubs ``save_config`` itself), and
    it records the marker as the real ``save_config`` would read it — that read
    is what the boot-step ordering has to get right.
    """
    from anki_miner.gui import main_window as mw_module

    recorded: list[tuple[object, str | None]] = []
    monkeypatch.setattr(
        mw_module.GUIConfigManager,
        "save_config",
        lambda cfg: recorded.append((cfg, GUIConfigManager.ACTIVE_PROFILE_ID)),
    )
    return recorded


def _window(qtbot, config):
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow(config)
    qtbot.addWidget(window)
    return window


@pytest.fixture
def opened_dialogs(monkeypatch) -> list:
    """Replace ``ProfileManagerDialog`` with a recorder, so nothing is shown."""
    created: list = []

    class _FakeDialog:
        def __init__(self, controller, on_profiles_changed, parent=None) -> None:
            self.controller = controller
            self.on_profiles_changed = on_profiles_changed
            self.parent = parent
            self.opened_with: list[str] = []
            created.append(self)

        def exec(self) -> int:
            self.opened_with.append("exec")
            return 0

        def show(self) -> None:
            self.opened_with.append("show")

    monkeypatch.setattr(dialog_module, "ProfileManagerDialog", _FakeDialog)
    return created


# ---------------------------------------------------------------------------
# Boot step
# ---------------------------------------------------------------------------


def test_bootstrap_runs_before_the_version_save(qtbot, monkeypatch, patch_heavy_init, quiet_boot, test_config):
    """The marker is already seeded when the last_known_version save fires."""
    config = replace(test_config, last_known_version="0.0.1")
    patch_heavy_init(config)
    saves = _record_saves(monkeypatch)
    window = _window(qtbot, config)

    window.commit_boot()

    # The version bump did save (otherwise the ordering claim is vacuous)...
    assert [cfg.last_known_version for cfg, _ in saves] == [__version__]
    # ...and every save this boot saw a profile marker. This is the regression a
    # step placed in either existing _run_optional_boot_step region causes.
    assert [marker for _, marker in saves] == ["default"]


def test_suppressed_boot_still_bootstraps(qtbot, monkeypatch, patch_heavy_init, quiet_boot, test_config):
    """The installer-smoke path needs the marker too — it asserts on the config file."""
    patch_heavy_init(test_config)
    _record_saves(monkeypatch)
    window = _window(qtbot, test_config)

    window.commit_boot(suppress_optional=True)

    assert GUIConfigManager.ACTIVE_PROFILE_ID == "default"
    assert (ProfileStore.profiles_dir() / "default.json").is_file()


def test_failing_bootstrap_does_not_break_boot(qtbot, monkeypatch, patch_heavy_init, quiet_boot, test_config):
    """The log-and-swallow wrapper keeps the rest of commit_boot running."""
    config = replace(test_config, last_known_version="0.0.1")
    patch_heavy_init(config)
    saves = _record_saves(monkeypatch)

    def _boom(self) -> None:
        raise OSError("profiles directory is unreadable")

    monkeypatch.setattr(ProfileController, "bootstrap", _boom)
    window = _window(qtbot, config)

    window.commit_boot()

    assert window._boot_committed is True
    assert [cfg.last_known_version for cfg, _ in saves] == [__version__]
    assert [marker for _, marker in saves] == [None]


# ---------------------------------------------------------------------------
# Signal wiring
# ---------------------------------------------------------------------------


def test_header_combo_reaches_switch_to(qtbot, monkeypatch, patch_heavy_init, test_config):
    switched: list[str] = []

    def _switch_to(self, profile_id: str) -> SwitchResult:
        switched.append(profile_id)
        return SwitchResult(switched=True)

    # Patched on the CLASS before construction: the connection binds the method
    # at _setup_ui time, so a later instance patch would not be seen.
    monkeypatch.setattr(ProfileController, "switch_to", _switch_to)
    patch_heavy_init(test_config)
    window = _window(qtbot, test_config)

    window.header.profile_changed.emit("anime")

    assert switched == ["anime"]


def test_header_sentinel_opens_the_manager_modally(qtbot, patch_heavy_init, test_config, opened_dialogs):
    patch_heavy_init(test_config)
    window = _window(qtbot, test_config)

    window.header.open_profile_manager.emit()

    assert len(opened_dialogs) == 1
    dialog = opened_dialogs[0]
    assert dialog.controller is window.profile_controller
    assert dialog.parent is window
    # exec(), never show(): the dialog sets no modality of its own, and a
    # modeless manager is repainted mid-CRUD by the settings reload a switch
    # fans out — the hazard this shape exists to avoid.
    assert dialog.opened_with == ["exec"]


def test_manager_callback_repoints_the_header(qtbot, monkeypatch, patch_heavy_init, test_config, opened_dialogs):
    """Rename/delete bypass the controller, so the window refreshes the combo."""
    patch_heavy_init(test_config)
    window = _window(qtbot, test_config)
    ProfileStore.write_profile("anime", test_config, name="Anime")
    ProfileStore.write_profile("novels", test_config, name="Novels")
    GUIConfigManager.ACTIVE_PROFILE_ID = "novels"

    calls: list[tuple[tuple[Profile, ...], str | None]] = []
    monkeypatch.setattr(window.header, "set_profiles", lambda profiles, active_id: calls.append((profiles, active_id)))
    window.header.open_profile_manager.emit()
    opened_dialogs[0].on_profiles_changed()

    assert calls == [((Profile("anime", "Anime"), Profile("novels", "Novels")), "novels")]


def test_settings_manage_button_opens_the_manager(wired_window, opened_dialogs):
    """Settings → UI → "Manage Profiles…" lands on the same window handler."""
    window, _titles, tabs = wired_window
    settings_tab = next(tab for tab in tabs.values() if isinstance(tab, SettingsTab))

    settings_tab.ui_panel.manage_profiles_btn.click()

    assert len(opened_dialogs) == 1
    assert opened_dialogs[0].controller is window.profile_controller
    assert opened_dialogs[0].opened_with == ["exec"]


# ---------------------------------------------------------------------------
# closeEvent
# ---------------------------------------------------------------------------


def test_close_writes_no_profile_file(qtbot, monkeypatch, patch_heavy_init, quiet_boot, test_config):
    """gui_config.json is authoritative while a profile is active (see the design note).

    A close-time snapshot would add a write and a failure path to a sequence
    that already has a deferred-close branch returning early, for no benefit.
    """
    patch_heavy_init(test_config)
    _record_saves(monkeypatch)
    window = _window(qtbot, test_config)
    window.commit_boot(suppress_optional=True)
    profiles_dir = ProfileStore.profiles_dir()
    listing_before = sorted(path.name for path in profiles_dir.iterdir())
    bytes_before = (profiles_dir / "default.json").read_bytes()

    window.closeEvent(QCloseEvent())

    assert sorted(path.name for path in profiles_dir.iterdir()) == listing_before
    assert (profiles_dir / "default.json").read_bytes() == bytes_before


def test_no_ui_session_state_reaches_a_profile_sidecar(
    qtbot, monkeypatch, patch_heavy_init, quiet_boot, test_config, tmp_path
):
    """Geometry, route and remembered folders are machine-local (D7).

    Switching profiles must not move the window or re-navigate the app, so a
    sidecar carries none of it — it holds an ``AnkiMinerConfig`` snapshot, and
    session state is not part of one.
    """
    from anki_miner.gui.utils import session_state

    patch_heavy_init(test_config)
    _record_saves(monkeypatch)
    window = _window(qtbot, test_config)
    window.commit_boot(suppress_optional=True)
    session_state.remember_accepted_path("reading.manga.inputs", str(tmp_path), file_mode=False)

    window.closeEvent(QCloseEvent())

    for sidecar in ProfileStore.profiles_dir().iterdir():
        raw = sidecar.read_text(encoding="utf-8")
        assert "reading.manga.inputs" not in raw
        assert "geometry" not in raw
        assert "main_tab" not in raw
    # The state really was written — just somewhere else.
    assert session_state.state_file().parent == ProfileStore.profiles_dir().parent
    assert session_state.remembered_directory("reading.manga.inputs") == str(tmp_path)
