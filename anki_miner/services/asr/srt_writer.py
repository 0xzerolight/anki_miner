"""Write ASR segments to an SRT subtitle file.

pysubs2 is a core dependency (already in ``[project.dependencies]``) so it
may be imported at the top level in Wave B — no guard needed.
"""

from __future__ import annotations

from pathlib import Path


def segments_to_srt(segments: list[tuple[float, float, str]], out_path: Path) -> None:
    """Write *segments* to *out_path* in SRT format.

    Args:
        segments: Ordered list of ``(start_s, end_s, text)`` tuples as
            returned by ``transcriber.transcribe``.
        out_path: Destination path for the SRT file; parent directory must
            exist before calling.

    Raises:
        NotImplementedError: Wave B fills the body.
    """
    raise NotImplementedError
