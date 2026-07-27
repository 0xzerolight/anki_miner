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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons, configure_data_view, install_copy_rows
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
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


class ProfileManagerDialog(ScreenIssueHost, QDialog):
    """Create, rename, delete and switch between named settings profiles.

    Args:
        controller: Sequencing for switch / create-from-current. It shows its
            own refusal dialog and re-points the header on every terminal path.
        on_profiles_changed: Called after a rename or a delete so the header
            combo picks the change up. The controller already does this for
            switch and create, so those paths do NOT call it again.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        controller: _ProfileSwitcher,
        on_profiles_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._on_profiles_changed = on_profiles_changed
        self._profiles: tuple[Profile, ...] = ()
        self._setup_ui()
        add_min_max_buttons(self)
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        # "Settings Profiles", never bare "Profiles": Anki has its own user
        # profiles, and this app's whole job is talking to Anki.
        self.setWindowTitle(self.tr("Settings Profiles"))
        self.setMinimumWidth(480)
        self.setMinimumHeight(420)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        header = QLabel(self.tr("Settings Profiles"))
        font = QFont()
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        layout.addWidget(header)

        helper = QLabel(
            self.tr(
                "A profile is a complete snapshot of every setting — dictionaries, filters, "
                "media, Anki fields, appearance. Switching swaps all of them at once, after "
                "saving your current settings back into the active profile."
            )
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.profile_list.itemSelectionChanged.connect(self._update_buttons)
        # Profiles are listed in store order, which is the order the user made
        # them in; sorting is deliberately never enabled here.
        configure_data_view(self.profile_list)
        install_copy_rows(self.profile_list)
        layout.addWidget(self.profile_list)

        buttons = QHBoxLayout()
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

        self.switch_button = ModernButton(self.tr("Switch To"), variant="primary")
        self.switch_button.clicked.connect(self._on_switch)
        buttons.addWidget(self.switch_button)

        close_button = ModernButton(self.tr("Close"), variant="secondary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)
        self.setLayout(layout)
        self.install_issue_banner(layout)

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
