"""Modal dialog for manual subtitle track selection."""

from __future__ import annotations

from anki_miner.gui.widgets.dialogs._track_picker_dialog import _TrackPickerDialog
from anki_miner.utils.audio_track_detector import SubtitleStream

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


class SubtitleTracksDialog(_TrackPickerDialog):
    """Let the user override the auto-detected subtitle track for a single mining run."""

    # ------------------------------------------------------------------
    # Stream accessors
    # ------------------------------------------------------------------

    def _index_of(self, stream: SubtitleStream) -> int:
        return stream.sub_index

    def _format_track_label(self, stream: SubtitleStream) -> str:
        return _format_track_label(stream)

    def _is_selectable(self, stream: SubtitleStream) -> bool:
        return stream.is_text

    # ------------------------------------------------------------------
    # Translatable strings — every literal keys on the SubtitleTracksDialog context
    # ------------------------------------------------------------------

    def _window_title(self) -> str:
        return self.tr("Subtitle Track")

    def _zero_track_text(self) -> str:
        return self.tr("No subtitle tracks found in this file.")

    def _single_track_text(self) -> str:
        return self.tr("This file has only one subtitle track.")

    def _auto_detected_template(self) -> str:
        return self.tr("Auto-detect (currently: Track %1 — %2)")

    def _auto_none_text(self) -> str:
        return self.tr("Auto-detect (no Japanese subtitle track found — will use first text track)")

    def _apply_button_text(self) -> str:
        return self.tr("Apply")
