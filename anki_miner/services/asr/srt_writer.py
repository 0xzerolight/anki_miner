"""Write ASR segments to an SRT subtitle file.

pysubs2 is a core dependency (already in ``[project.dependencies]``) so it
may be imported at the top level in Wave B — no guard needed.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2


def segments_to_srt(segments: list[tuple[float, float, str]], out_path: Path) -> None:
    """Write *segments* to *out_path* in SRT format.

    Segments with zero duration or empty text (after stripping) are skipped.
    Timings are converted from seconds to milliseconds and rounded.

    Args:
        segments: Ordered list of ``(start_s, end_s, text)`` tuples as
            returned by ``transcriber.transcribe``.
        out_path: Destination path for the SRT file; parent directory must
            exist before calling.
    """
    subs = pysubs2.SSAFile()
    for start_s, end_s, text in segments:
        text = text.strip()
        start_ms = round(start_s * 1000)
        end_ms = round(end_s * 1000)
        if start_ms == end_ms or not text:
            continue
        event = pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text)
        subs.append(event)
    subs.save(str(out_path), format_="srt")
