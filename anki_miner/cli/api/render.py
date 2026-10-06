"""``render``: phase 5's notes built and their media copied into the run folder, nothing sent to Anki (API.md)."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import CardPayload
from anki_miner.services.anki_media_store import AnkiMediaStore, _build_store_media_action
from anki_miner.services.anki_service import AnkiService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rendered:
    """One word's note: its fields by Anki field name, and the media files they name."""

    fields: dict[str, str]
    files: list[str]


class LocalMediaStore(AnkiMediaStore):
    """``store_batch``'s naming without Anki: each file copied into *folder* under the name Anki would give it."""

    def __init__(self, config: AnkiMinerConfig, folder: Path) -> None:
        super().__init__(config)
        self._folder = folder

    def store_files(self, paths_by_filename: dict[str, Path]) -> dict[str, str]:
        """Each file under the name ``store_files`` would send Anki, refused by the same size and read checks."""
        self._folder.mkdir(parents=True, exist_ok=True)
        stored: dict[str, str] = {}
        for filename, path in paths_by_filename.items():
            action = _build_store_media_action(filename, path, content_hash=True, by_path=True)
            if action is None:  # it logged why (unreadable, over the cap)
                continue
            name = action["params"]["filename"]
            shutil.copyfile(path, self._folder / name)
            stored[filename] = name
        return stored

    def upload_dict_media(self, word_data_list: list[CardPayload]) -> None:
        """Dictionary images are not copied (API.md, render)."""


class RenderService(AnkiService):
    """Phase 5's Anki for ``render``: each payload's note built and its media copied; no note is added."""

    def __init__(self, config: AnkiMinerConfig, folder: Path) -> None:
        super().__init__(config)
        self._media_store = LocalMediaStore(config, folder)
        #: mined_form -> its note, for the words phase 5 reached
        self.rendered: dict[str, Rendered] = {}

    def create_cards_batch(
        self, word_data_list: list[CardPayload], progress_callback: ProgressCallback | None = None
    ) -> list[int]:
        """No duplicate check and no addNotes: the notes go to ``rendered``. Returns no note ids."""
        self._reset_last_run()
        self.rendered = {}
        stored = self._media_store.store_batch(word_data_list)
        self.last_media_store_failures = self._media_store.last_store_failures
        for item in word_data_list:
            note = self._build_note(item, stored).note
            media = item.media
            names = (media.screenshot_filename, media.audio_filename, media.expression_audio_filename)
            self.rendered[item.word.mined_form] = Rendered(
                fields=dict(note["fields"]), files=[name for name in names if name and name in stored]
            )
        logger.info("Render: %d note(s), %d file(s) stored", len(self.rendered), len(stored))
        return []
