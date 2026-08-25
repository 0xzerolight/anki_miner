"""Manager for recently processed file pairs."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.bounded_reader import read_json_bounded

logger = logging.getLogger(__name__)

_RECENTS_MAX_BYTES = 256 * 1024


class RecentFilesManager:
    """Manages a list of recently processed video/subtitle file pairs.

    Stores entries in a JSON file at ~/.anki_miner/recent_files.json.
    """

    def __init__(self, max_items: int = 10):
        """Initialize the recent files manager.

        Args:
            max_items: Maximum number of recent entries to store.
        """
        self._max_items = max_items
        self._file_path = ANKI_MINER_HOME / "recent_files.json"

    def add_entry(self, video_path: Path, subtitle_path: Path, subtitle_offset: float = 0.0) -> None:
        """Add a video/subtitle pair to recent files.

        Deduplicates by (video, subtitle) pair. If the pair already exists,
        it is moved to the top with an updated timestamp.

        Args:
            video_path: Path to the video file.
            subtitle_path: Path to the subtitle file.
            subtitle_offset: Subtitle timing offset in seconds, restored when the
                pair is re-selected. Defaults to 0.0.
        """
        entries = self._load()

        # Remove existing entry with same pair (dedup)
        video_str = str(video_path)
        subtitle_str = str(subtitle_path)
        entries = [e for e in entries if not (e["video"] == video_str and e["subtitle"] == subtitle_str)]

        # Prepend new entry
        entries.insert(
            0,
            {
                "video": video_str,
                "subtitle": subtitle_str,
                "subtitle_offset": float(subtitle_offset),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

        # Trim to max_items
        entries = entries[: self._max_items]

        self._save(entries)

    def get_recent(self) -> list[dict]:
        """Get the list of recent file pairs.

        Returns:
            List of dicts with keys: video, subtitle, subtitle_offset, timestamp.
            Ordered most recent first.
        """
        return self._load()

    def clear(self) -> None:
        """Remove all recent file entries."""
        if self._file_path.exists():
            self._file_path.unlink()

    def _load(self) -> list[dict]:
        """Load entries from the JSON file."""
        if not self._file_path.exists():
            return []
        data = read_json_bounded(self._file_path, _RECENTS_MAX_BYTES, None, "recent files")
        if not isinstance(data, list):
            return []
        entries = data[: self._max_items]
        if not all(
            isinstance(entry, dict)
            and isinstance(entry.get("video"), str)
            and isinstance(entry.get("subtitle"), str)
            and (
                "subtitle_offset" not in entry
                or isinstance(entry["subtitle_offset"], (int, float))
                and not isinstance(entry["subtitle_offset"], bool)
            )
            for entry in entries
        ):
            logger.warning("Invalid recent files entry; using empty list")
            return []
        return entries

    def _save(self, entries: list[dict]) -> None:
        """Save entries to the JSON file.

        Atomic: stage to a unique sibling temp file then ``os.replace`` so a
        crash mid-write can't truncate the existing list. The temp name must
        be unique (not a shared fixed ``.tmp``) because nothing prevents two
        processes from racing this save; a shared name lets their writes
        interleave and byte-splice a corrupt file. OSErrors are logged rather
        than swallowed silently — a bare ``except: pass`` left the user with no
        signal that their recent list had stopped persisting.
        """
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_write_path(self._file_path) as tmp_path:
                tmp_path.write_text(
                    json.dumps(entries, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except OSError as e:
            logger.warning("Could not save recent files list: %s", e)
