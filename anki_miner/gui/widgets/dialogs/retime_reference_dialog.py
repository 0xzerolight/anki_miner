"""Modal dialog for picking what subtitle retiming aligns against.

Retiming has one reference concept, not two. alass accepts either an embedded
subtitle track or audio, and the auto policy prefers a subtitle track, so the
picker lists both kinds in one flat list — subtitle tracks first, in the order
:func:`~anki_miner.services.retime_reference.list_reference_subtitle_streams`
would try them, then audio tracks.

The shared picker keys its radios on one integer, so each row is wrapped in a
:class:`ReferenceChoice` carrying a flat ``position``; the caller turns the
chosen position back into a
:class:`~anki_miner.services.retime_reference.ReferenceOverride`.
"""

from __future__ import annotations

from dataclasses import dataclass

from anki_miner.gui.widgets.dialogs._track_picker_dialog import _TrackPickerDialog
from anki_miner.services.retime_reference import ReferenceKind, ReferenceOverride
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream
from anki_miner.utils.i18n import tr_format


@dataclass(frozen=True)
class ReferenceChoice:
    """One selectable row in the reference picker.

    ``position`` is the row's index in the list handed to the dialog — the
    integer the shared picker round-trips. ``stream_index`` is what the retimer
    actually needs: a ``sub_index`` for subtitles, an audio index for audio.
    """

    position: int
    kind: ReferenceKind
    stream_index: int
    language_tag: str | None
    title: str | None
    codec: str | None
    selectable: bool

    def to_override(self) -> ReferenceOverride:
        """Return the retimer-facing override this row stands for."""
        return ReferenceOverride(kind=self.kind, index=self.stream_index)


def build_reference_choices(
    subtitle_streams: list[SubtitleStream],
    audio_streams: list[AudioStream],
) -> list[ReferenceChoice]:
    """Flatten both stream lists into picker rows, subtitles first.

    *subtitle_streams* is expected in reference-preference order (what
    ``list_reference_subtitle_streams`` returns) so the list the user reads top
    to bottom matches the order auto-selection would try. Bitmap subtitle
    streams are included but marked unselectable — hiding them would leave a
    user wondering where their track went.
    """
    choices: list[ReferenceChoice] = []
    for stream in subtitle_streams:
        choices.append(
            ReferenceChoice(
                position=len(choices),
                kind="subtitle",
                stream_index=stream.sub_index,
                language_tag=stream.language_tag,
                title=stream.title,
                codec=stream.codec_name,
                selectable=stream.is_text,
            )
        )
    for audio in audio_streams:
        choices.append(
            ReferenceChoice(
                position=len(choices),
                kind="audio",
                stream_index=audio.audio_index,
                language_tag=audio.language_tag,
                title=audio.title_tag,
                codec=audio.codec,
                selectable=True,
            )
        )
    return choices


class RetimeReferenceDialog(_TrackPickerDialog):
    """Let the user override the auto-selected retiming reference for one run."""

    # ------------------------------------------------------------------
    # Stream accessors
    # ------------------------------------------------------------------

    def _index_of(self, stream: ReferenceChoice) -> int:
        return stream.position

    def _format_track_label(self, stream: ReferenceChoice) -> str:
        template = (
            self.tr("Subtitle track %1 - %2 - %3") if stream.kind == "subtitle" else self.tr("Audio track %1 - %2 - %3")
        )
        label = tr_format(
            template,
            str(stream.stream_index + 1),
            stream.language_tag or "und",
            (stream.codec or "?").upper(),
        )
        if stream.title:
            label += f" ({stream.title})"
        if not stream.selectable:
            label += self.tr(" - image-based, cannot be used")
        return label

    def _is_selectable(self, stream: ReferenceChoice) -> bool:
        return stream.selectable

    # ------------------------------------------------------------------
    # Translatable strings — every literal keys on the RetimeReferenceDialog context
    # ------------------------------------------------------------------

    def _window_title(self) -> str:
        return self.tr("Alignment Reference")

    def _zero_track_text(self) -> str:
        return self.tr("This file has no audio or subtitle tracks to align against.")

    def _single_track_text(self) -> str:
        return self.tr("This file has only one track to align against.")

    def _auto_detected_template(self) -> str:
        # Callers always pass auto_detected=None: which track wins is only known
        # after the reference is extracted and cleaned, which happens during the
        # run, not while this dialog is open. The hook stays implemented so the
        # base class contract holds if that ever changes.
        return self.tr("Auto (currently: track %1 - %2)")

    def _auto_none_text(self) -> str:
        return self.tr("Auto - best embedded subtitle track, or audio if there is none")

    def _apply_button_text(self) -> str:
        return self.tr("Apply")
