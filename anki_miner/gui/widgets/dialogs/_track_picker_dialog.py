"""Shared skeleton for the audio/subtitle track-picker dialogs.

``AudioTracksDialog`` and ``SubtitleTracksDialog`` are a name-normalized clone:
the zero/single/multi-track layout, button-group wiring, ``_AUTO_BUTTON_ID``
handling, preselect logic, ``_on_accept`` and ``selected_override`` are byte-for
-byte identical once the ``AudioStream``/``SubtitleStream`` field names are
folded away. This base hoists that tr-free body; the two subclasses supply the
per-stream accessors and — critically — every ``self.tr()`` literal.

tr()-CONTEXT RULE: this module contains NO ``self.tr()`` call on purpose. Every
translatable string lives in a string-returning hook overridden in the subclass,
so pylupdate6 keeps keying each literal on its ``AudioTracksDialog`` /
``SubtitleTracksDialog`` context. Hoisting a ``self.tr()`` here would re-home the
string onto ``_TrackPickerDialog`` and empty out all 12 catalogs on the next
extract. Keep it tr-free.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.utils.i18n import tr_format

_AUTO_BUTTON_ID = 10_000  # sentinel button-group ID for the Auto radio (avoids Qt's reserved -1/-2)


class _TrackPickerDialog(QDialog):
    """Internal base for the single-run audio/subtitle track override dialogs.

    Subclasses provide the stream accessors (:meth:`_index_of`,
    :meth:`_format_track_label`, :meth:`_is_selectable`) and the translatable
    string hooks (title / zero / single / auto / apply). ``streams`` and
    ``auto_detected`` are ``AudioStream``/``SubtitleStream`` instances handled
    only through those hooks, so the base never touches a field name.
    """

    def __init__(
        self,
        streams: list[Any],
        current_override: int | None,
        auto_detected: Any | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self._window_title())
        self.setMinimumWidth(400)

        # _result holds the stream index to return, or None for Auto.
        # Initialised to None; multi-track variant sets it to current_override
        # so reject() leaves it unchanged. Single/zero-track variants always
        # return None (no meaningful override exists for degenerate track counts).
        self._result: int | None = None
        self._button_group: QButtonGroup | None = None

        layout = QVBoxLayout(self)

        if len(streams) == 0:
            self._build_zero_track(layout)
        elif len(streams) == 1:
            self._build_single_track(layout, streams[0])
        else:
            self._build_multi_track(layout, streams, current_override, auto_detected)

        add_min_max_buttons(self)

    # ------------------------------------------------------------------
    # Subclass hooks — stream accessors
    # ------------------------------------------------------------------

    def _index_of(self, stream: Any) -> int:
        """Return the stream's selectable index (audio_index / sub_index)."""
        raise NotImplementedError

    def _format_track_label(self, stream: Any) -> str:
        """Return the per-track radio label for ``stream``."""
        raise NotImplementedError

    def _is_selectable(self, stream: Any) -> bool:
        """Return whether ``stream`` can be chosen (bitmap subs are not)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Subclass hooks — translatable strings (each MUST hold a self.tr() literal)
    # ------------------------------------------------------------------

    def _window_title(self) -> str:
        raise NotImplementedError

    def _zero_track_text(self) -> str:
        raise NotImplementedError

    def _single_track_text(self) -> str:
        raise NotImplementedError

    def _auto_detected_template(self) -> str:
        """Return the ``tr_format`` template for the auto radio (has %1/%2)."""
        raise NotImplementedError

    def _auto_none_text(self) -> str:
        """Return the auto-radio label when no track was auto-detected."""
        raise NotImplementedError

    def _apply_button_text(self) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_zero_track(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel(self._zero_track_text()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_single_track(self, layout: QVBoxLayout, stream: Any) -> None:
        layout.addWidget(QLabel(self._single_track_text()))
        layout.addWidget(QLabel(self._format_track_label(stream)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_multi_track(
        self,
        layout: QVBoxLayout,
        streams: list[Any],
        current_override: int | None,
        auto_detected: Any | None,
    ) -> None:
        # Only selectable tracks yield a valid override; bitmap subs are shown disabled.
        valid_indices = {self._index_of(s) for s in streams if self._is_selectable(s)}
        self._result = current_override if current_override in valid_indices else None
        self._button_group = QButtonGroup(self)
        self._radio_map: dict[int, QRadioButton] = {}  # stream index → radio

        # Auto radio
        if auto_detected is not None:
            lang = auto_detected.language_tag or "und"
            auto_text = tr_format(
                self._auto_detected_template(),
                self._index_of(auto_detected) + 1,
                lang,
            )
        else:
            auto_text = self._auto_none_text()
        auto_radio = QRadioButton(auto_text)
        self._button_group.addButton(auto_radio, _AUTO_BUTTON_ID)
        layout.addWidget(auto_radio)

        # One radio per stream; unselectable rows are disabled
        for stream in streams:
            radio = QRadioButton(self._format_track_label(stream))
            if not self._is_selectable(stream):
                radio.setEnabled(False)
            idx = self._index_of(stream)
            self._button_group.addButton(radio, idx)
            self._radio_map[idx] = radio
            layout.addWidget(radio)

        # Preselect — only selectable tracks; otherwise fall back to Auto
        target = self._radio_map.get(current_override) if current_override is not None else None
        if target is not None and target.isEnabled():
            target.setChecked(True)
        else:
            auto_radio.setChecked(True)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(self._apply_button_text())
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        if self._button_group is None:
            self.accept()
            return
        button_id = self._button_group.checkedId()
        self._result = None if button_id == _AUTO_BUTTON_ID else button_id
        self.accept()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_override(self) -> int | None:
        """Return the stream index to use, or None for Auto-detect."""
        return self._result
