"""Modal dialog for manual audio track selection."""

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

from anki_miner.utils.audio_track_detector import AudioStream
from anki_miner.utils.i18n import tr_format

_AUTO_BUTTON_ID = 10_000  # sentinel button-group ID for the Auto radio (avoids Qt's reserved -1/-2)


def _format_channels(channels: int | None) -> str:
    """Return a human-readable channel-layout string, or '' if channels is None."""
    if channels is None:
        return ""
    mapping = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
    return mapping.get(channels, f"{channels}ch")


def _format_track_label(stream: AudioStream) -> str:
    language = stream.language_tag or "und"
    codec = (stream.codec or "?").upper()
    ch_layout = _format_channels(stream.channels)
    parts = f"Track {stream.audio_index + 1} — {language} · {codec}"
    if ch_layout:
        parts += f" {ch_layout}"
    if stream.title_tag:
        parts += f" ({stream.title_tag})"
    return parts


class AudioTracksDialog(QDialog):
    """Let the user override the auto-detected audio track for a single mining run."""

    def __init__(
        self,
        streams: list[AudioStream],
        current_override: int | None,
        auto_detected: AudioStream | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Audio Track"))
        self.setMinimumWidth(400)

        # _result holds the audio_index to return, or None for Auto.
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

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_zero_track(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel(self.tr("No audio tracks found in this file.")))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_single_track(self, layout: QVBoxLayout, stream: AudioStream) -> None:
        layout.addWidget(QLabel(self.tr("This file has only one audio track.")))
        layout.addWidget(QLabel(_format_track_label(stream)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_multi_track(
        self,
        layout: QVBoxLayout,
        streams: list[AudioStream],
        current_override: int | None,
        auto_detected: AudioStream | None,
    ) -> None:
        valid_indices = {s.audio_index for s in streams}
        self._result = current_override if current_override in valid_indices else None
        self._button_group = QButtonGroup(self)
        self._radio_map: dict[int, QRadioButton] = {}  # audio_index → radio

        # Auto radio
        if auto_detected is not None:
            lang = auto_detected.language_tag or "und"
            auto_text = tr_format(
                self.tr("Auto-detect (currently: Track %1 — %2)"),
                auto_detected.audio_index + 1,
                lang,
            )
        else:
            auto_text = self.tr("Auto-detect (no Japanese track found — will use first track)")
        auto_radio = QRadioButton(auto_text)
        self._button_group.addButton(auto_radio, _AUTO_BUTTON_ID)
        layout.addWidget(auto_radio)

        # One radio per stream
        for stream in streams:
            radio = QRadioButton(_format_track_label(stream))
            self._button_group.addButton(radio, stream.audio_index)
            self._radio_map[stream.audio_index] = radio
            layout.addWidget(radio)

        # Preselect
        if current_override is None:
            auto_radio.setChecked(True)
        else:
            target = self._radio_map.get(current_override)
            if target is not None:
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
        """Return the audio_index to use, or None for Auto-detect."""
        return self._result
