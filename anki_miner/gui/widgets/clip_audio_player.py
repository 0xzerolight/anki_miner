"""Audio-only libmpv player for short clip files (Word Curator, Anki-deck runs).

Deck cards carry their own sentence clip as a file, one per card, so the
curator's video player (one source, seek by time) does not fit. This plays one
file at a time from its start on a ``video=False`` core, the same display-free
options the preview uses when the GL surface is off (see create_mpv_player).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from anki_miner.utils.mpv_loader import create_mpv_player, terminate_mpv_player


class ClipAudioPlayer:
    """Plays one clip at a time; built on first play, released once by the owner."""

    def __init__(self, factory: Callable[..., Any] = create_mpv_player) -> None:
        self._factory = factory
        self._player: Any = None

    def play(self, path: Path) -> None:
        """Play ``path`` from its start, replacing whatever was playing."""
        if self._player is None:
            self._player = self._factory(video=False)
        self._player.loadfile(str(path))
        self._player.pause = False

    def stop(self) -> None:
        """Pause playback (no-op before the first play)."""
        if self._player is not None:
            self._player.pause = True

    def release(self) -> None:
        """Terminate the core; safe to call more than once."""
        player, self._player = self._player, None
        if player is not None:
            terminate_mpv_player(player)
