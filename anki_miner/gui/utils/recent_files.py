"""Manager for recently processed file pairs."""

import json
from datetime import datetime, timezone
from pathlib import Path


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
        self._file_path = Path.home() / ".anki_miner" / "recent_files.json"

    def add_entry(self, video_path: Path, subtitle_path: Path) -> None:
        """Add a video/subtitle pair to recent files.

        Deduplicates by (video, subtitle) pair. If the pair already exists,
        it is moved to the top with an updated timestamp.

        Args:
            video_path: Path to the video file.
            subtitle_path: Path to the subtitle file.
        """
        entries = self._load()

        # Remove existing entry with same pair (dedup)
        video_str = str(video_path)
        subtitle_str = str(subtitle_path)
        entries = [
            e for e in entries if not (e["video"] == video_str and e["subtitle"] == subtitle_str)
        ]

        # Prepend new entry
        entries.insert(
            0,
            {
                "video": video_str,
                "subtitle": subtitle_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Trim to max_items
        entries = entries[: self._max_items]

        self._save(entries)

    def get_recent(self) -> list[dict]:
        """Get the list of recent file pairs.

        Returns:
            List of dicts with keys: video, subtitle, timestamp.
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
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self, entries: list[dict]) -> None:
        """Save entries to the JSON file."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
