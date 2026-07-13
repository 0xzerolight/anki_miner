"""Modal dialog for manual audio track selection."""

from __future__ import annotations

from anki_miner.gui.widgets.dialogs._track_picker_dialog import _TrackPickerDialog
from anki_miner.utils.audio_track_detector import AudioStream


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


class AudioTracksDialog(_TrackPickerDialog):
    """Let the user override the auto-detected audio track for a single mining run."""

    # ------------------------------------------------------------------
    # Stream accessors
    # ------------------------------------------------------------------

    def _index_of(self, stream: AudioStream) -> int:
        return stream.audio_index

    def _format_track_label(self, stream: AudioStream) -> str:
        return _format_track_label(stream)

    def _is_selectable(self, stream: AudioStream) -> bool:
        return True

    # ------------------------------------------------------------------
    # Translatable strings — every literal keys on the AudioTracksDialog context
    # ------------------------------------------------------------------

    def _window_title(self) -> str:
        return self.tr("Audio Track")

    def _zero_track_text(self) -> str:
        return self.tr("No audio tracks found in this file.")

    def _single_track_text(self) -> str:
        return self.tr("This file has only one audio track.")

    def _auto_detected_template(self) -> str:
        return self.tr("Auto-detect (currently: Track %1 — %2)")

    def _auto_none_text(self) -> str:
        return self.tr("Auto-detect (no Japanese track found — will use first track)")

    def _apply_button_text(self) -> str:
        return self.tr("Apply")
