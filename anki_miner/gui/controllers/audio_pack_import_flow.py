"""Audio pack import orchestration (add / per-row reimport).

Mirrors :class:`~anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportFlow`.
Owns the :class:`~anki_miner.gui.workers.audio_pack_import_worker.AudioPackImportWorker`
lifecycle and every dialog in the import flows.  The tab keeps the panel
widgets, the signal wiring, and the narrow chain persist
(``_persist_audio_chain_change``), injected here as callables so the
dependency stays one-way: tab → controller → workers/services.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.workers.audio_pack_import_worker import AudioPackImportWorker
from anki_miner.services.audio_packs.formats import scan_importable_packs
from anki_miner.services.audio_packs.importer import derive_pack_id

# Upstream source priority for newly imported packs inserted into the chain.
# Lower index = higher priority (queried first).  Keys are canonical pack_ids
# as returned by _derive_pack_id (which maps canonical folder names such as
# "nhk16_files" → "nhk16", "forvo_files" → "forvo", etc.).
# Unknown pack_ids sort after all known ones (stable).
_PACK_PRIORITY: dict[str, int] = {
    "nhk16": 0,
    "shinmeikai8": 1,
    "forvo": 2,
    "jpod": 3,
    "jpod_alternate": 4,
}


class AudioPackImportFlow:
    """Drives audio pack directory imports for the Settings → Audio panel.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab).
        panel: The audio pack settings panel (chain state, registry refresh).
        get_config: Returns the tab's *current* config.
        persist_chain: The tab's narrow chain persist
            (``SettingsTab._persist_audio_chain_change``) — saves a chain
            mutation to disk and notifies listeners without running the full
            Save pipeline.
    """

    def __init__(
        self,
        parent: QWidget,
        panel: AudioPackSettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[tuple[AudioSourceEntry, ...]], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        # Long-lived worker reference: AudioPackImportWorker is a QThread and
        # would be destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: AudioPackImportWorker | None = None

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle import-trigger buttons. Prevents overlapping import workers."""
        # no panel-level reimport button; context-menu rows stay enabled
        self._panel._add_btn.setEnabled(enabled)

    def _chain_with_new_packs_inserted(self, new_pack_ids: list[str]) -> tuple[AudioSourceEntry, ...]:
        """Return current chain with new packs inserted above first enabled jpod101.

        Priority order: newly imported packs are placed ABOVE the first enabled
        jpod101 entry (or appended at the end if none), preserving upstream
        priority order among the batch itself.  Existing entries are preserved
        and any duplicate pack_id is removed before re-insertion so a re-added
        pack appears at the priority slot rather than duplicating.
        """
        current = list(self._panel.get_chain())

        # Remove any pre-existing entries with the same pack_id so a re-added
        # pack takes the new priority slot rather than appearing twice.
        current = [e for e in current if e.kind == "jpod101" or e.pack_id not in new_pack_ids]

        new_entries = [AudioSourceEntry(kind="pack", pack_id=pid, enabled=True) for pid in new_pack_ids]

        # Find the first enabled jpod101 entry to insert before it.
        insert_idx: int | None = None
        for i, entry in enumerate(current):
            if entry.kind == "jpod101" and entry.enabled:
                insert_idx = i
                break

        if insert_idx is not None:
            current[insert_idx:insert_idx] = new_entries
        else:
            current.extend(new_entries)

        return tuple(current)

    def add_pack(self) -> None:
        """Prompt for a directory and import all detectable audio packs in it."""
        chosen_dir = QFileDialog.getExistingDirectory(self._parent, "Choose audio pack folder", "")
        if not chosen_dir:
            return

        packs = scan_importable_packs(Path(chosen_dir))
        # Sort by upstream source priority so completion order = priority order
        # and _chain_with_new_packs_inserted preserves the correct sequence.
        # Unknown pack_ids land after all known ones (stable sort).
        packs.sort(key=lambda pd_fmt: _PACK_PRIORITY.get(derive_pack_id(pd_fmt[0].name), len(_PACK_PRIORITY)))
        if not packs:
            QMessageBox.warning(
                self._parent,
                "No Audio Packs Found",
                f"No recognisable audio packs were found in:\n{chosen_dir}\n\n"
                "Supported formats: AJT (index.json + media/), NHK16 (entries.json + audio/), "
                "Forvo (speaker subdirectories), JPod legacy ({reading} - {expression} stems).",
            )
            return

        # Import all detected packs sequentially using the same chained
        # state-machine pattern as DictionaryImportFlow.reimport_all.
        dest_root = self._get_config().audio_packs_root
        dlg = QProgressDialog("Importing audio pack…", "Cancel", 0, 0, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        self._set_import_buttons_enabled(False)

        state: dict[str, object] = {
            "index": 0,
            "cancelled": False,
            "imported": [],  # list of pack_id strings
            "errors": [],  # list of (display_name, error_msg) tuples
        }

        def finish() -> None:
            dlg.close()
            self._set_import_buttons_enabled(True)

            assert isinstance(state["imported"], list)
            imported: list[str] = state["imported"]
            assert isinstance(state["errors"], list)
            errors: list[tuple[str, str]] = state["errors"]

            if imported:
                new_chain = self._chain_with_new_packs_inserted(imported)
                self._panel.refresh_registry()
                self._panel.set_chain(new_chain)
                self._persist_chain(new_chain)

            if len(packs) == 1 and not errors:
                # Single pack — no summary needed; the registry refresh is feedback enough.
                return

            # Multi-pack batch: show summary dialog.
            lines: list[str] = []
            if imported:
                lines.append(f"Imported {len(imported)} audio pack{'s' if len(imported) != 1 else ''}:")
                lines.extend(f"  • {pid}" for pid in imported)
            if errors:
                if lines:
                    lines.append("")
                lines.append("Failed:")
                lines.extend(f"  • {name}: {msg}" for name, msg in errors)
            if state["cancelled"]:
                if lines:
                    lines.append("")
                lines.append("Cancelled before remaining packs.")

            QMessageBox.information(self._parent, "Audio Packs Added", "\n".join(lines) or "Done.")

        def launch_next() -> None:
            idx = state["index"]
            assert isinstance(idx, int)
            if state["cancelled"] or idx >= len(packs):
                finish()
                return

            pack_dir, _fmt = packs[idx]
            dlg.setLabelText(f"Pack {idx + 1} of {len(packs)}: {pack_dir.name}")

            worker = AudioPackImportWorker.for_pack(pack_dir, dest_root)
            # Join the predecessor before dropping its reference (same as
            # DictionaryImportFlow.reimport_all T-09 join rationale).
            prev = self._active_import_worker
            if prev is not None and prev.isRunning():
                prev.wait()
            self._active_import_worker = worker

            def on_progress(msg: str) -> None:
                dlg.setLabelText(msg)

            def on_done(pack_id: str, _meta: dict) -> None:
                assert isinstance(state["imported"], list)
                state["imported"].append(pack_id)
                state["index"] = idx + 1
                launch_next()

            def on_failed(err: str) -> None:
                assert isinstance(state["errors"], list)
                state["errors"].append((pack_dir.name, err))
                state["index"] = idx + 1
                launch_next()

            worker.progress.connect(on_progress)
            worker.import_finished.connect(on_done)
            worker.failed.connect(on_failed)
            worker.start()

        def on_cancel() -> None:
            state["cancelled"] = True
            w = self._active_import_worker
            if w is not None and w.isRunning():
                w.cancel()

        dlg.canceled.connect(on_cancel)
        launch_next()

    def reimport_pack(self, pack_id: str) -> None:
        """Prompt for the pack's source directory and re-import with overwrite.

        Fixes moved-folder scenarios: the user picks the new location and the
        importer overwrites the existing index in-place, preserving the
        pack_id so the chain entry keeps pointing at it correctly.
        """
        chosen_dir = QFileDialog.getExistingDirectory(self._parent, "Choose audio pack folder to re-import", "")
        if not chosen_dir:
            return

        dest_root = self._get_config().audio_packs_root
        dlg = QProgressDialog("Re-importing audio pack…", "Cancel", 0, 100, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = AudioPackImportWorker.for_pack(Path(chosen_dir), dest_root, pack_id=pack_id, overwrite=True)
        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(msg: str) -> None:
            dlg.setLabelText(msg)

        def on_done(imported_id: str, _meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self._parent,
                "Audio Pack Re-imported",
                f"Re-imported {imported_id} successfully.",
            )
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self._parent, "Re-import Failed", err)
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()
