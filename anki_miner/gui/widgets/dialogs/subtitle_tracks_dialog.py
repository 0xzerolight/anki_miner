"""Modal dialog for manual subtitle track selection."""

from __future__ import annotations

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
from anki_miner.utils.audio_track_detector import SubtitleStream
from anki_miner.utils.i18n import tr_format

_AUTO_BUTTON_ID = 10_000  # sentinel button-group ID for the Auto radio (avoids Qt's reserved -1/-2)

# Appended to bitmap (image-based) subtitle rows; these carry rendered pixels,
# not extractable text, so they cannot be condensed and are shown disabled.
_BITMAP_ANNOTATION = "image-based — cannot condense"


def _format_track_label(stream: SubtitleStream) -> str:
    language = stream.language_tag or "und"
    codec = (stream.codec_name or "?").upper()
    parts = f"Track {stream.sub_index + 1} — {language} · {codec}"
    if stream.title:
        parts += f" ({stream.title})"
    if not stream.is_text:
        parts += f" — {_BITMAP_ANNOTATION}"
    return parts


class SubtitleTracksDialog(QDialog):
    """Let the user override the auto-detected subtitle track for a single mining run."""

    def __init__(
        self,
        streams: list[SubtitleStream],
        current_override: int | None,
        auto_detected: SubtitleStream | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Subtitle Track"))
        self.setMinimumWidth(400)

        # _result holds the sub_index to return, or None for Auto.
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
    # Layout builders
    # ------------------------------------------------------------------

    def _build_zero_track(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel(self.tr("No subtitle tracks found in this file.")))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_single_track(self, layout: QVBoxLayout, stream: SubtitleStream) -> None:
        layout.addWidget(QLabel(self.tr("This file has only one subtitle track.")))
        layout.addWidget(QLabel(_format_track_label(stream)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_multi_track(
        self,
        layout: QVBoxLayout,
        streams: list[SubtitleStream],
        current_override: int | None,
        auto_detected: SubtitleStream | None,
    ) -> None:
        # Only text tracks are selectable; bitmap rows are shown disabled.
        valid_indices = {s.sub_index for s in streams if s.is_text}
        self._result = current_override if current_override in valid_indices else None
        self._button_group = QButtonGroup(self)
        self._radio_map: dict[int, QRadioButton] = {}  # sub_index → radio

        # Auto radio
        if auto_detected is not None:
            lang = auto_detected.language_tag or "und"
            auto_text = tr_format(
                self.tr("Auto-detect (currently: Track %1 — %2)"),
                auto_detected.sub_index + 1,
                lang,
            )
        else:
            auto_text = self.tr("Auto-detect (no Japanese subtitle track found — will use first text track)")
        auto_radio = QRadioButton(auto_text)
        self._button_group.addButton(auto_radio, _AUTO_BUTTON_ID)
        layout.addWidget(auto_radio)

        # One radio per stream; bitmap rows are disabled (cannot be condensed)
        for stream in streams:
            radio = QRadioButton(_format_track_label(stream))
            if not stream.is_text:
                radio.setEnabled(False)
            self._button_group.addButton(radio, stream.sub_index)
            self._radio_map[stream.sub_index] = radio
            layout.addWidget(radio)

        # Preselect — only text tracks are selectable, otherwise fall back to Auto
        target = self._radio_map.get(current_override) if current_override is not None else None
        if target is not None and target.isEnabled():
            target.setChecked(True)
        else:
            auto_radio.setChecked(True)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(self.tr("Apply"))
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
        """Return the sub_index to use, or None for Auto-detect."""
        return self._result
