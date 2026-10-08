"""Modal CRUD surface for named settings profiles.

Deliberately a modal dialog rather than a group inside the Settings tab: a
profile switch fans ``config_refreshed`` out to every settings panel, which
repaints them mid-interaction. Doing create/rename/delete inside a panel that
the switch is simultaneously reloading is the hazard this shape avoids.

Division of labour, mirroring :mod:`anki_miner.gui.controllers.profile_controller`:

* switching and "new from current" are SEQUENCING, so they go through the
  controller — which also owns the refusal dialog and the header snap-back, so
  this dialog must never raise a second dialog for the same refusal;
* rename and delete are pure STORAGE, so they call :class:`ProfileStore`
  directly and surface its ``ValueError`` themselves.

Policy that ``ProfileStore`` deliberately does not enforce ("not the active
one", "not the last one", confirmation) lives here, as the caller.

It also carries the live settings' Export / Import / Reset (D14, UI/UX audit
2026-09-29), handed in by the window as ``settings_actions``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.gui.utils.qt_helpers import configure_data_view, install_copy_rows
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.controllers.profile_controller import SwitchResult


class _ProfileSwitcher(Protocol):
    """The two controller methods this dialog drives.

    Named here rather than typing the constructor as ``ProfileController`` so
    the dialog stays testable with a fake and cannot reach into ``MainWindow``
    through the controller by accident. ``ProfileController`` implements both.
    """

    def switch_to(self, profile_id: str) -> SwitchResult: ...

    def create_from_current(self, name: str) -> SwitchResult: ...


class SettingsFileActions(Protocol):
    """The whole-profile actions the "This profile" row runs (D14).

    ``SettingsTab`` implements all three; ``surface`` is this dialog, so their
    file pickers, confirmations and failures show over it rather than behind.
    """

    def export_settings(self, surface: QWidget) -> None: ...

    def import_settings(self, surface: QWidget) -> None: ...

    def reset_settings(self, surface: QWidget) -> None: ...


class ProfileManagerDialog(ScreenIssueHost, EnhancedDialog):
    """Create, rename, delete and switch between named settings profiles.

    Args:
        controller: Sequencing for switch / create-from-current. It shows its
            own refusal dialog and re-points the header on every terminal path.
        on_profiles_changed: Called after a rename or a delete so the header
            combo picks the change up. The controller already does this for
            switch and create, so those paths do NOT call it again.
        parent: Optional parent widget.
        settings_actions: Export, Import and Reset of the live settings (D14).
            ``None`` hides that row.
    """

    def __init__(
        self,
        controller: _ProfileSwitcher,
        on_profiles_changed: Callable[[], None],
        parent: QWidget | None = None,
        *,
        settings_actions: SettingsFileActions | None = None,
    ) -> None:
        super().__init__(parent, title=self.tr("Settings Profiles"))
        self._controller = controller
        self._on_profiles_changed = on_profiles_changed
        self._profiles: tuple[Profile, ...] = ()
        self._settings_actions = settings_actions
        # Not _setup_ui: EnhancedDialog.__init__ already ran its own frame builder.
        self._build_content()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_content(self) -> None:
        # "Settings Profiles", never bare "Profiles": Anki has its own user
        # profiles, and this app's whole job is talking to Anki.
        self.setMinimumHeight(420)
        self.set_header(
            "",
            self.tr("Settings Profiles"),
            self.tr(
                "A profile is a complete snapshot of every setting — dictionaries, filters, "
                "media, Anki fields, appearance. Switching swaps all of them at once, after "
                "saving your current settings back into the active profile."
            ),
        )

        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.profile_list.itemSelectionChanged.connect(self._update_buttons)
        # Profiles are listed in store order, which is the order the user made
        # them in; sorting is deliberately never enabled here.
        configure_data_view(self.profile_list)
        install_copy_rows(self.profile_list)
        self.add_content(self.profile_list, 1)

        # Row actions act on the selected profile. Switch To is secondary: the
        # dialog's one primary is Close, its only way out (C19, D41).
        self._actions_row = QWidget()
        self._actions_row.setObjectName("profile-manager-row")
        buttons = QHBoxLayout(self._actions_row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(SPACING.sm)
        self.new_button = ModernButton(self.tr("New from Current…"), variant="secondary")
        self.new_button.setToolTip(self.tr("Save the settings you are using now as a new profile and switch to it."))
        self.new_button.clicked.connect(self._on_new)
        buttons.addWidget(self.new_button)
        self.rename_button = ModernButton(self.tr("Rename…"), variant="secondary")
        self.rename_button.clicked.connect(self._on_rename)
        buttons.addWidget(self.rename_button)
        self.delete_button = ModernButton(self.tr("Delete"), variant="critical")
        self.delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        self.switch_button = ModernButton(self.tr("Switch To"), variant="secondary")
        self.switch_button.clicked.connect(self._on_switch)
        buttons.addWidget(self.switch_button)
        self.add_content(self._actions_row)

        # D14: Export, Import and Reset of the live settings, moved here from
        # the footer every Settings page used to carry. They act on the active
        # profile's settings, exactly as the footer did.
        self._settings_row = QWidget()
        self._settings_row.setObjectName("profile-manager-row")
        settings_layout = QHBoxLayout(self._settings_row)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(SPACING.sm)
        settings_layout.addWidget(QLabel(self.tr("This profile:")))
        self.export_settings_button = ModernButton(self.tr("Export to file…"), variant="secondary")
        self.export_settings_button.setToolTip(
            self.tr("Save a portable settings file (machine-specific paths and resources excluded).")
        )
        self.export_settings_button.clicked.connect(lambda: self._run_settings_action("export"))
        settings_layout.addWidget(self.export_settings_button)
        self.import_settings_button = ModernButton(self.tr("Import from file…"), variant="secondary")
        self.import_settings_button.setToolTip(
            self.tr("Apply settings from an exported file; anything not in the file is kept.")
        )
        self.import_settings_button.clicked.connect(lambda: self._run_settings_action("import"))
        settings_layout.addWidget(self.import_settings_button)
        self.reset_settings_button = ModernButton(self.tr("Reset to defaults…"), variant="secondary")
        self.reset_settings_button.setToolTip(self.tr("Your installed resources and your theme are kept."))
        self.reset_settings_button.clicked.connect(lambda: self._run_settings_action("reset"))
        settings_layout.addWidget(self.reset_settings_button)
        settings_layout.addStretch()
        self._settings_row.setVisible(self._settings_actions is not None)
        self.add_content(self._settings_row)

        self.add_close_button()
        # A clean Import or Reset confirms here (P-B3.7a): the Settings row that
        # flashes it otherwise is behind this modal dialog. Left of Close, so it
        # never widens a button row; the objectName gives it the tab flash's style.
        self.status_label = QLabel("")
        self.status_label.setObjectName("settings-save-status")
        self.footer_layout.insertWidget(0, self.status_label)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status_label.setText(""))
        self.install_issue_banner(self._main_layout, 1)
        self._fit_minimum_width()

    def flash_status(self, text: str) -> None:
        """Show a short confirmation beside Close, for as long as the Settings flash shows."""
        self.status_label.setText(text)
        self._status_timer.start(2500)

    def _run_settings_action(self, which: str) -> None:
        actions = self._settings_actions
        if actions is None:
            return
        if which == "export":
            actions.export_settings(self)
        elif which == "import":
            actions.import_settings(self)
        else:
            actions.reset_settings(self)

    def _fit_minimum_width(self) -> None:
        """Never narrower than the widest button row (C19): "New from Current…",
        "Rename…" and "Switch To" clipped at the old fixed 480px in long locales."""
        margins = self._main_layout.contentsMargins()
        widest = max(
            (row.sizeHint().width() for row in self.findChildren(QWidget, "profile-manager-row")),
            default=0,
        )
        widest = max(widest, self._actions_row.sizeHint().width())
        self.setMinimumWidth(max(480, widest + margins.left() + margins.right()))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self, select_id: str | None = None) -> None:
        """Reload the list from the store, keeping (or moving) the selection.

        Read straight from ``ProfileStore``/``GUIConfigManager`` rather than
        from a snapshot taken at construction: a switch started from this dialog
        moves the active id, and profile files can also change under a
        long-open dialog.
        """
        wanted = select_id or self._selected_id()
        active_id = GUIConfigManager.ACTIVE_PROFILE_ID
        self._profiles = ProfileStore.list_profiles()

        self.profile_list.clear()
        for profile in self._profiles:
            label = tr_format(self.tr("%1 (active)"), profile.name) if profile.id == active_id else profile.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_list.addItem(item)
            if profile.id == wanted:
                self.profile_list.setCurrentItem(item)

        self._update_buttons()

    def _selected_id(self) -> str | None:
        item = self.profile_list.currentItem()
        if item is None or not item.isSelected():
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, str) else None

    def _selected_profile(self) -> Profile | None:
        profile_id = self._selected_id()
        return next((profile for profile in self._profiles if profile.id == profile_id), None)

    def _update_buttons(self) -> None:
        """Apply the caller-side policy the store deliberately does not enforce."""
        selected = self._selected_id()
        is_active = selected is not None and selected == GUIConfigManager.ACTIVE_PROFILE_ID
        self.rename_button.setEnabled(selected is not None)
        # Switching to the profile you are already on is a no-op; deleting it
        # would leave the live config attributed to a file that is gone, and
        # deleting the last one would leave no profile at all.
        self.switch_button.setEnabled(selected is not None and not is_active)
        self.delete_button.setEnabled(selected is not None and not is_active and len(self._profiles) > 1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, self.tr("New Profile"), self.tr("Name for the new profile:"))
        if not ok or not name.strip():
            return
        # create_from_current already reports every refusal — a name the store
        # rejects included — through its own QMessageBox, and re-points the
        # header. A second dialog here would double every failure.
        self._controller.create_from_current(name)
        self._refresh()

    def _on_rename(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        name, ok = QInputDialog.getText(
            self,
            self.tr("Rename Profile"),
            tr_format(self.tr("New name for '%1':"), profile.name),
            text=profile.name,
        )
        if not ok or not name.strip():
            return
        try:
            ProfileStore.rename(profile.id, name)
        except (OSError, ValueError) as exc:
            # Blank name, case-insensitive duplicate, or an unwritable file.
            self._warn(self.tr("The profile could not be renamed."), exc)
            return
        self._refresh(select_id=profile.id)
        self._on_profiles_changed()

    def _on_delete(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        reply = QMessageBox.question(
            self,
            self.tr("Delete Profile"),
            tr_format(
                self.tr("Delete the profile '%1'? Its saved settings cannot be recovered."),
                profile.name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            ProfileStore.delete(profile.id)
        except (OSError, ValueError) as exc:
            self._warn(self.tr("The profile could not be deleted."), exc)
            self._refresh()
            return
        self._refresh()
        self._on_profiles_changed()

    def _on_switch(self) -> None:
        profile_id = self._selected_id()
        if profile_id is None:
            return
        # Refusals (and a switch that landed but could not fully refresh the
        # window) are already surfaced by the controller; this dialog only has
        # to re-render whatever the session ended on.
        self._controller.switch_to(profile_id)
        self._refresh()

    def _warn(self, summary: str, error: Exception) -> None:
        """Report a profile operation that failed, inside the dialog (D24).

        ``summary`` is the whole sentence; the exception is the diagnostic and
        stays behind Details.
        """
        self.show_screen_issue(ScreenIssue(summary=summary, details=str(error)))
