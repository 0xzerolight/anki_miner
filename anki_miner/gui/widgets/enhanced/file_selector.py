"""File selector widget with integrated browse button and validation."""

from collections.abc import Callable, Iterable
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils import file_dialogs, session_state
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets.base import ElidingLabel, make_label_fit_text
from anki_miner.utils.i18n import tr_format

#: A drop validator answers "may this path land here, and if not, why not".
#: The reason is shown to the user verbatim, so it is a sentence, not a code.
DropValidator = Callable[[Path], "tuple[bool, str]"]


def accepts_suffixes(extensions: Iterable[str], reason: str) -> DropValidator:
    """Build a drop validator admitting only files with the given extensions.

    Every screen's rule is the same shape -- "this field takes a video" -- so it
    is written once here and fed the screen's OWN extension set. There is no
    second extension registry: callers pass ``FilePairMatcher.VIDEO_EXTENSIONS``,
    the condenser's media set, and so on.

    Args:
        extensions: Suffixes including the dot, in any case.
        reason: The already-translated sentence shown when a path is refused.
            Each screen supplies its own so the string stays in its tr-context.

    Returns:
        A validator for :class:`FileSelector`'s ``drop_validator``.
    """
    allowed = {extension.lower() for extension in extensions}

    def validate(path: Path) -> tuple[bool, str]:
        return path.suffix.lower() in allowed, reason

    return validate


class FileSelector(QWidget):
    """Enhanced file selector with validation and drag-drop support.

    Features:
    - Integrated label, input, and browse button
    - File validation with visual indicators
    - Drag-and-drop support
    - Shows current file/folder name below input
    - Emits signals when path changes or is validated

    Signals:
        path_changed: Emitted when path changes (str: new_path)
        path_validated: Emitted when path is validated (bool: is_valid, str: path)
        drop_rejected: Emitted with the reason a drop was refused (str)
    """

    path_changed = pyqtSignal(str)  # new path
    path_validated = pyqtSignal(bool, str)  # is_valid, path
    drop_rejected = pyqtSignal(str)  # human-readable reason

    def __init__(
        self,
        label: str = "File:",
        file_mode: bool = True,
        file_filter: str = "All Files (*)",
        placeholder: str = "",
        label_width: int | None = None,
        default_dir: Path | str | None = None,
        optional: bool = False,
        history_key: str | None = None,
        drop_validator: DropValidator | None = None,
        parent=None,
    ):
        """Initialize the file selector.

        Args:
            label: Label text
            file_mode: True for file selection, False for folder selection
            file_filter: File filter for dialog (only used if file_mode=True)
            placeholder: Placeholder text for input field
            label_width: Fixed width for the label column. When set, every
                selector in a section can share one width so their input fields
                line up. When None, falls back to a 100px minimum.
            default_dir: Default directory the Browse dialog opens at when the field is empty.
            optional: When True, a non-empty but ABSENT path renders the
                neutral state ("Not installed") instead of the red error
                border — for optional resources whose default path simply
                doesn't exist yet on a clean install (Issue #100). Validity
                reporting (``path_validated``/``is_valid``) is unchanged.
            history_key: Stable identifier for the workflow and role this
                selector serves (e.g. ``"reading.manga.inputs"``). When set,
                Browse reopens in the folder last ACCEPTED under that key and
                records each new acceptance there (D7). Selectors without a key
                behave exactly as before, which is how Settings, profiles and
                Deck Builder stay out of the history.
            drop_validator: Decides whether a dragged path may land here, and
                supplies the sentence shown when it may not (D50). The kind
                check -- file versus folder, one local path -- is already done
                before this runs, so a consumer only states its own rule, e.g.
                "this field takes a video file". Without one, any local path of
                the right kind is accepted, which is what every selector did
                before drops were validated at all.
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._file_mode = file_mode
        self._file_filter = file_filter
        self._placeholder = placeholder
        self._label_width = label_width
        self._default_dir = default_dir
        self._optional = optional
        self._history_key = history_key
        self._drop_validator = drop_validator
        self._is_valid = False

        self._label_text = label
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        # Main row: Label + Input + Browse button
        main_layout = QHBoxLayout()
        main_layout.setSpacing(SPACING.xs)

        # Label - only add if label text is not empty
        self.label: QLabel | None = None
        if self._label_text:
            self.label = QLabel(self._label_text)
            self.label.setObjectName("field-label")
            if self._label_width is not None:
                # Minimum, not fixed: text size is applied LIVE (Settings -> UI)
                # without rebuilding tabs, so a width frozen at construction is
                # stale the moment the user scales text -- which is how a 105px
                # box ended up holding 274px of German. A minimum lets the label
                # grow to its recomputed sizeHint (the neighbouring input is
                # Expanding and yields the space); the tradeoff is that an
                # over-long label breaks column alignment instead of clipping.
                self.label.setMinimumWidth(self._label_width)
            else:
                self.label.setMinimumWidth(100)
            make_label_fit_text(self.label)
            main_layout.addWidget(self.label)

        # Input field
        self.input = QLineEdit()

        # Set placeholder text
        if self._placeholder:
            placeholder = self._placeholder
        elif self._file_mode:
            placeholder = self.tr("Select file...")
        else:
            placeholder = self.tr("Select folder...")

        self.input.setPlaceholderText(placeholder)
        self.input.textChanged.connect(self._on_text_changed)
        main_layout.addWidget(self.input)

        # Browse button
        self.browse_button = QPushButton(self.tr("Browse..."))
        self.browse_button.clicked.connect(self._on_browse_clicked)
        main_layout.addWidget(self.browse_button)

        layout.addLayout(main_layout)

        # Status label (shows current file/folder name or validation message).
        # ElideMiddle keeps the file extension visible when a long name is truncated.
        self.status_label = ElidingLabel("", mode=Qt.TextElideMode.ElideMiddle)
        self.status_label.setObjectName("caption")
        status_font = QFont()
        status_font.setPixelSize(FONT_SIZES.caption)
        self.status_label.setFont(status_font)
        layout.addWidget(self.status_label)

        self.setLayout(layout)

        # Set size policy to prevent compression
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # Enable drag and drop
        self.setAcceptDrops(True)

        # Set accessibility properties
        file_or_folder = self.tr("file") if self._file_mode else self.tr("folder")
        self.setAccessibleName(self._label_text)
        self.setAccessibleDescription(
            tr_format(self.tr("Select a %1 by typing path, browsing, or dragging"), file_or_folder)
        )

        self.input.setAccessibleName(tr_format(self.tr("%1 path"), self._label_text))
        self.input.setAccessibleDescription(
            tr_format(self.tr("Path to %1. Type or paste a path, or use browse button"), file_or_folder)
        )

        self.browse_button.setAccessibleName(tr_format(self.tr("Browse for %1"), file_or_folder))
        self.browse_button.setAccessibleDescription(
            tr_format(self.tr("Opens file dialog to select %1"), file_or_folder)
        )

        # Set proper tab order
        self.setTabOrder(self.input, self.browse_button)

        # Initial status
        self._update_status()

    def _on_text_changed(self, text: str) -> None:
        """Handle input text change.

        Args:
            text: New text value
        """
        self._validate_path(text)
        self.path_changed.emit(text)

    def browse(self) -> None:
        """Open file/folder browser dialog.

        This is a public method that can be called from keyboard shortcuts.
        """
        self._on_browse_clicked()

    def _on_browse_clicked(self) -> None:
        """Handle browse button click.

        A non-empty return is the ONLY thing that moves the remembered folder.
        Typing, dropping, ``set_path`` and a cancelled dialog are not statements
        about where the user keeps this kind of file, so they leave it alone.
        """
        start_dir = resolve_start_dir(
            # Never .strip() a filesystem-bound path: a folder whose name ends
            # in a space is a real folder (the batch-mining core dump).
            self.path_or_none(),
            file_mode=self._file_mode,
            remembered_dir=session_state.remembered_directory(self._history_key),
            default_dir=self._default_dir,
        )
        if self._file_mode:
            # File selection
            file_path, _ = file_dialogs.get_open_file_name(
                self,
                tr_format(self.tr("Select %1"), self._label_text),
                start_dir,
                self._file_filter,
            )
            if file_path:
                session_state.remember_accepted_path(self._history_key, file_path, file_mode=True)
                self.input.setText(file_path)
                self.input.setCursorPosition(0)
                self.input.setToolTip(file_path)
        else:
            # Folder selection
            folder_path = file_dialogs.get_existing_directory(
                self,
                tr_format(self.tr("Select %1"), self._label_text),
                start_dir,
            )
            if folder_path:
                session_state.remember_accepted_path(self._history_key, folder_path, file_mode=False)
                self.input.setText(folder_path)
                self.input.setCursorPosition(0)
                self.input.setToolTip(folder_path)

    def _validate_path(self, path_str: str) -> None:
        """Validate the provided path.

        Args:
            path_str: Path string to validate
        """
        if not path_str:
            self._is_valid = False
            self.input.setProperty("error", False)
            self.input.setProperty("success", False)
            self._update_status()
            self.path_validated.emit(False, "")
            return

        path = Path(path_str)

        is_valid = path.is_file() if self._file_mode else path.is_dir()

        self._is_valid = is_valid

        # Update input styling. An optional resource whose path is simply
        # absent shows the neutral state, not the red error border (its
        # validity still reports False below — only the styling differs).
        self.input.setProperty("error", not is_valid and not self._optional)
        self.input.setProperty("success", is_valid)

        # Force style refresh
        if style := self.input.style():
            style.unpolish(self.input)
            style.polish(self.input)

        self._update_status()
        self.path_validated.emit(is_valid, path_str)

    def _update_status(self) -> None:
        """Update the helper row under the input, and whether it is there at all.

        The row only appears when it has something the user can act on. A blank
        field is not a fault -- announcing "No file selected" under all 31 pickers
        made every untouched form read as unfinished, and spent a row of height
        per picker saying so -- and a valid path is already spelled out in the
        input above it. Both keep their text for tooltips and diagnostics; they
        just stop taking up space.

        ``ElidingLabel`` owns truncation, tooltip, and re-elision on resize, so we just
        hand it the full text.
        """
        path_str = self.input.text()

        if not path_str:
            self.status_label.setText(self.tr("No file selected") if self._file_mode else self.tr("No folder selected"))
            actionable = False
        elif self._is_valid:
            self.status_label.setText(Path(path_str).name)
            actionable = False
        elif self._optional:
            self.status_label.setText(self.tr("Not installed"))
            actionable = True
        else:
            self.status_label.setText(
                self.tr("File not found. Choose an existing file.")
                if self._file_mode
                else self.tr("Folder not found. Choose an existing folder.")
            )
            actionable = True

        self.status_label.setVisible(actionable)
        # A hidden child is skipped by the layout entirely, so this widget's
        # height changes when the row comes and goes -- the parent form has to be
        # told, or it keeps laying the selector out at its previous size.
        self.updateGeometry()

    def get_path(self) -> str:
        """Get the current path.

        Returns:
            Current path string
        """
        return self.input.text()

    def path_or_none(self) -> str | None:
        """Return the RAW path text, or ``None`` when the field is effectively empty.

        Emptiness is judged on the *stripped* text (whitespace-only counts as
        empty), but the returned string is the un-stripped text that
        :meth:`_validate_path` already validated — so a real path whose name
        legitimately ends (or begins) with a space is preserved verbatim.

        Callers must not ``.strip()`` this before handing it to ``Path``/the
        filesystem: stripping desyncs the path from what ``is_valid()`` checked
        and, for a directory whose name ends in a space, produces a
        nonexistent path (the batch-mining core-dump, Issue: trailing-space
        media folder).
        """
        raw = self.get_path()
        return raw if raw.strip() else None

    def set_path(self, path: str) -> None:
        """Set the path.

        Args:
            path: Path to set
        """
        self.input.setText(path)
        self.input.setCursorPosition(0)
        self.input.setToolTip(path)

    def is_valid(self) -> bool:
        """Check if current path is valid.

        Returns:
            True if path is valid, False otherwise
        """
        return self._is_valid

    def clear(self) -> None:
        """Clear the current path."""
        self.input.clear()

    # ------------------------------------------------------------------
    # Drag and drop (decision D50)
    # ------------------------------------------------------------------
    #
    # A drop used to be accepted sight unseen: any URL, of any kind, set the
    # field. Dropping a subtitle on the video picker therefore "worked" and then
    # failed at run time, and dropping a folder on a file picker did nothing
    # visible at all. Now the target states what it will do while the drag is
    # still in the air, and a payload it cannot take is refused with a reason.

    def _classify_drop(self, event: QDragEnterEvent | QDropEvent) -> tuple[Path | None, str]:
        """Decide what a dragged payload is. Exactly one half of the pair is set.

        Returns:
            ``(path, "")`` when the payload may land here, or ``(None, reason)``
            with the sentence the user is shown.
        """
        urls = urls_from_event(event)
        if not urls:
            return None, self.tr("Only files and folders can be dropped here.")
        if len(urls) > 1:
            return None, self.tr("Drop one item at a time.")

        # Never .strip() a filesystem-bound path: a folder whose name ends in a
        # space is a real folder.
        local = urls[0].toLocalFile()
        if not local:
            return None, self.tr("Only local files can be dropped here.")

        path = Path(local)
        if self._file_mode and path.is_dir():
            return None, self.tr("That is a folder; this field takes a file.")
        if not self._file_mode and path.is_file():
            return None, self.tr("That is a file; this field takes a folder.")

        if self._drop_validator is not None:
            accepted, reason = self._drop_validator(path)
            if not accepted:
                return None, reason
        return path, ""

    def _drop_invitation(self) -> str:
        """What the lit-up target says it will take.

        Uses the field's own label when it has one -- "Drop Video File here"
        names the destination, which is the whole point of lighting it up.
        """
        label = self._label_text.rstrip(":").strip()
        if label:
            return tr_format(self.tr("Drop %1 here"), label)
        return self.tr("Drop the file here") if self._file_mode else self.tr("Drop the folder here")

    def _set_drop_state(self, state: str, message: str) -> None:
        """Light the field for the drag in progress and say what will happen.

        Args:
            state: ``"valid"``, ``"invalid"`` or ``""`` to clear.
            message: The line shown under the field while the state holds.
        """
        self.input.setProperty("dropState", state)
        if style := self.input.style():
            style.unpolish(self.input)
            style.polish(self.input)

        if state:
            self.status_label.setText(message)
            self.status_label.setVisible(True)
            self.updateGeometry()
        else:
            # Hand the row back to whoever owns it when no drag is in flight.
            self._update_status()

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        """Light the field, and accept only a payload this field can take."""
        if event is None:
            return
        path, reason = self._classify_drop(event)
        if path is None:
            # Accepting the action is what buys the drop event, and the drop is
            # what lets the refusal be spoken rather than silently swallowed.
            self._set_drop_state("invalid", reason)
            event.acceptProposedAction()
            return
        self._set_drop_state("valid", self._drop_invitation())
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        """Put the field back the way it was when the drag moves off it."""
        self._set_drop_state("", "")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        """Take the path, or refuse it out loud. Never leave the field lit.

        Re-classified rather than trusting the drag-enter verdict: the payload
        under the cursor can change between enter and release.

        A drop is not a statement about where the user keeps this kind of file,
        so -- like ``set_path`` and unlike a Browse that returned a path -- it
        does not touch the remembered-folder history (D7).
        """
        if event is None:
            return
        path, reason = self._classify_drop(event)
        self._set_drop_state("", "")
        if path is None:
            self.status_label.setText(reason)
            self.status_label.setVisible(True)
            self.updateGeometry()
            self.drop_rejected.emit(reason)
            event.ignore()
            return
        self.set_path(str(path))
        event.acceptProposedAction()
