"""Add-flow controller for the YouTube tab: gate, probe, classify, expand.

Extracted from :mod:`anki_miner.gui.widgets.youtube_tab` so the tab keeps only
queue/run/widget concerns. :class:`PlaylistAddController` owns the *Add* side
of the YouTube queue:

* Input gating (T-34): :func:`_is_acceptable_add_input` rejects option-leading
  tokens before classification, on both the single-video and playlist paths.
* Single videos: one :class:`YouTubeProbeWorker` per Add, run in parallel;
  results are classified by :func:`_classify_probe_result` into READY or
  PROBE_ERROR.
* Playlists (Issue #70): playlist-shaped URLs spawn a
  :class:`YouTubePlaylistResolveWorker` (flat-playlist probe), the user
  confirms via :meth:`PlaylistAddController._ask_playlist_choice`, and the
  chosen entries are expanded into ordinary queue rows whose metadata is
  filled in sequentially by a single :class:`YouTubePlaylistProbeWorker`.
  At most one playlist may be resolving/probing at a time.

The controller is a plain (non-QObject) collaborator constructed and driven on
the GUI thread; every signal connection below is made on the GUI thread, so
PyQt's slot proxies deliver worker emissions back onto the GUI thread exactly
as they did when these slots lived on the tab. It reaches back into the tab
only through :class:`PlaylistAddCallbacks`. Queue *run* semantics (the frozen
``_run_items`` idx mapping, mid-run skip-set wiring) stay in the tab — this
class never touches a running :class:`YouTubeQueueWorker`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.youtube_playlist_probe_worker import (
    YouTubePlaylistProbeWorker,
    YouTubePlaylistResolveWorker,
)
from anki_miner.gui.workers.youtube_probe_worker import YouTubeProbeWorker
from anki_miner.models.youtube import PlaylistEntry, PlaylistInfo, SubMode, VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.youtube_url import YouTubeUrlInfo, classify_youtube_url

# Bare 11-char YouTube video id (same alphabet as the fetcher's _VIDEO_ID_RE).
_BARE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _is_acceptable_add_input(url: str) -> bool:
    """Reject inputs that would reach yt-dlp as an *option*, not a URL (T-34).

    The fetcher already inserts a ``--`` end-of-options separator, but this
    is belt-and-braces: a ``-``/``--``-leading token (``--update-to=…`` etc.)
    is never a real video reference, so refuse it before it is queued.

    Accept when the input is an explicit ``http(s)`` URL (yt-dlp stays the
    final validator for non-YouTube-shaped URLs), a YouTube-classified URL,
    or a bare 11-char video id. Everything else — option-leading tokens,
    other schemes, junk — is rejected.
    """
    candidate = url.strip()
    if not candidate:
        return False
    scheme = urlparse(candidate).scheme.lower()
    if scheme in ("http", "https"):
        return True
    if _BARE_VIDEO_ID_RE.match(candidate):
        return True
    return classify_youtube_url(candidate).kind != "unknown"


def _classify_probe_result(info: VideoInfo, config: AnkiMinerConfig) -> tuple[bool, str | None, SubMode | None]:
    """Classify a probe result.

    Returns:
        (is_mineable, error_message, resolved_sub_mode). On success
        ``is_mineable`` is True, ``error_message`` is None, and
        ``resolved_sub_mode`` is the chosen sub mode. On failure the
        triple's first element is False and ``error_message`` describes
        why the video cannot be mined.
    """
    if info.is_live:
        return False, "Live streams are not supported.", None
    if info.duration_s > config.youtube_max_duration_s:
        minutes_limit = max(1, config.youtube_max_duration_s // 60)
        return False, f"Video exceeds max duration ({minutes_limit} min).", None
    if info.is_age_restricted and not (config.youtube_cookies_from_browser or config.youtube_cookies_file):
        # Either cookies source bypasses YouTube's age gate; the fetcher's
        # _cookie_args() honors both --cookies-from-browser and --cookies <file>.
        return (
            False,
            "Age-restricted video. Set Cookies (Browser or File) in Settings and retry.",
            None,
        )
    if info.has_manual_ja_subs:
        return True, None, "manual_only"
    if info.has_auto_ja_subs:
        return True, None, "auto_only"
    return False, "No Japanese subtitles available for this video.", None


@dataclass(frozen=True)
class PlaylistAddCallbacks:
    """Tab-side seams the add-flow controller calls back into.

    Every callable runs on the GUI thread. The queue accessors wrap the tab's
    :class:`~anki_miner.models.youtube_queue.YouTubeQueue` so the controller
    never holds the model itself — adds and membership checks stay visible at
    the tab boundary.
    """

    enqueue: Callable[[str], YouTubeQueueItem]
    """``YouTubeQueue.add`` — create a PENDING item for a URL."""

    queued_items: Callable[[], list[YouTubeQueueItem]]
    """``YouTubeQueue.all_items`` — snapshot of the live queue."""

    render_new_item: Callable[[YouTubeQueueItem], None]
    """Create the row widget for a freshly queued item."""

    refresh_row: Callable[[YouTubeQueueItem], None]
    """Re-render a row after the item's fields changed."""

    recompute_buttons: Callable[[], None]
    """Re-derive button enabled/visible state from queue + workers."""

    clear_url_input: Callable[[], None]
    """Clear the URL line edit after an accepted Add."""

    log_info: Callable[[str], None]
    log_warning: Callable[[str], None]
    log_error: Callable[[str], None]


class PlaylistAddController:
    """Owns the YouTube tab's add flow: probes, playlist expansion, dialog.

    State owned here (moved off the tab):

    * the in-flight single-video probe workers,
    * the at-most-one playlist resolve / playlist entry-probe workers and the
      frozen ``_playlist_probe_items`` idx snapshot,
    * the ``_playlist_generation`` counter that drops late resolves,
    * a frozen :class:`AnkiMinerConfig` snapshot (refreshed via
      :meth:`update_config` alongside the fetcher).

    The tab drives it through four entry points: :meth:`begin` on Add,
    :attr:`is_active` from its button recomputation, :meth:`invalidate_pending`
    on Clear, and :meth:`shutdown` from ``YouTubeTab.shutdown()``.
    """

    def __init__(
        self,
        fetcher: YouTubeFetcherService,
        config: AnkiMinerConfig,
        callbacks: PlaylistAddCallbacks,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            fetcher: YouTube fetcher service used for metadata probes.
            config: Frozen application configuration snapshot.
            callbacks: Tab-side seams (queue access, row rendering, logging).
            parent: Widget used as Qt parent for spawned worker threads and
                as the playlist choice dialog's parent — the tab, preserving
                the pre-extraction QObject ownership tree.
        """
        self._fetcher = fetcher
        self._config = config
        self._callbacks = callbacks
        self._parent = parent

        # In-flight probe workers — kept alive until they finish.
        self._probe_workers: list[YouTubeProbeWorker] = []

        # Playlist expansion state (Issue #70). At most one playlist may be
        # resolving or probing at a time; begin() warns and refuses a second.
        self._playlist_resolve_worker: YouTubePlaylistResolveWorker | None = None
        self._playlist_probe_worker: YouTubePlaylistProbeWorker | None = None
        # Frozen snapshot of the items handed to the playlist probe worker,
        # indexed by its per-entry idx signals — same identity-based pattern
        # as the tab's _run_items/_item_at (YouTubeQueueItem is eq=False).
        self._playlist_probe_items: list[YouTubeQueueItem] = []
        # Bumped on Clear so a late playlist_resolved from a pre-Clear Add is
        # ignored instead of popping a dialog over an emptied queue.
        self._playlist_generation = 0

    # ------------------------------------------------------------------
    # Tab-facing API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True while a playlist resolve is pending.

        This is the phase during which the tab must lock its Add button — a
        second Add mid-resolve would race the confirmation dialog. Entry
        probing does *not* count: single videos may still be added while a
        playlist's entries are being probed (a second *playlist* is refused
        inside :meth:`begin` instead).
        """
        return self._playlist_resolve_worker is not None

    def begin(self, url: str) -> None:
        """Add *url* — single videos probe directly, playlists resolve first."""
        # Reject option-leading / non-URL inputs before they reach yt-dlp as
        # an argument. Leave the field populated so the user can fix it. T-34.
        if not _is_acceptable_add_input(url):
            self._callbacks.log_error("Not a valid YouTube URL or video id. Paste a youtube.com / youtu.be link.")
            return

        url_info = classify_youtube_url(url)
        if url_info.kind in ("playlist", "video_in_playlist"):
            if self._playlist_resolve_worker is not None or self._playlist_probe_worker is not None:
                self._callbacks.log_warning("A playlist is already being added — wait for it to finish.")
                return
            self._begin_playlist_resolve(url, url_info)
            return

        # "video" and "unknown" both fall through to the single-video probe
        # path — yt-dlp remains the final validator for unrecognised URLs.
        # Normalise a bare 11-char video id to a canonical watch URL so that
        # item.url always classifies correctly in playlist dedup (OVH-036).
        if _BARE_VIDEO_ID_RE.match(url.strip()):
            url = f"https://www.youtube.com/watch?v={url.strip()}"
        self._add_single_url(url)

    def invalidate_pending(self) -> None:
        """Invalidate pending playlist work on Clear.

        Bumps the generation so a late ``playlist_resolved`` from a pre-Clear
        Add is dropped instead of popping a dialog over the queue the user
        just emptied, and cancels an active playlist entry probe — cancel()
        only; never wait() on the GUI thread. The finished slot clears the
        handle + snapshot.
        """
        self._playlist_generation += 1
        if self._playlist_probe_worker is not None:
            self._playlist_probe_worker.cancel()

    def update_config(self, config: AnkiMinerConfig, fetcher: YouTubeFetcherService) -> None:
        """Adopt a new frozen config + rebuilt fetcher (Settings change).

        In-flight workers are unaffected — they captured the old fetcher at
        construction; only probes spawned after this call use the new one.
        """
        self._config = config
        self._fetcher = fetcher

    def shutdown(self) -> None:
        """Tear down probe + playlist workers (tab shutdown).

        Single-video probes are bounded by their subprocess timeout, so
        quit() + wait() returns within ~timeout_s. The playlist probe worker
        checks cancellation between entries and each in-flight subprocess is
        timeout-bounded, so wait() has the same guarantee (Issue #70).
        """
        for probe in list(self._probe_workers):
            probe.quit()
            probe.wait()
        self._probe_workers.clear()

        if self._playlist_probe_worker is not None:
            self._playlist_probe_worker.cancel()
            self._playlist_probe_worker.quit()
            self._playlist_probe_worker.wait()
            self._playlist_probe_worker = None
        self._playlist_probe_items = []
        if self._playlist_resolve_worker is not None:
            self._playlist_resolve_worker.quit()
            self._playlist_resolve_worker.wait()
            self._playlist_resolve_worker = None

    # ------------------------------------------------------------------
    # Single-video probe lifecycle
    # ------------------------------------------------------------------

    def _add_single_url(self, url: str) -> None:
        """Queue *url* as a single video and spawn a metadata probe worker."""
        item = self._callbacks.enqueue(url)
        # The queue model defaults to PENDING; flip to PROBING up-front so the
        # row widget renders the "(probing...)" hint immediately.
        item.status = YouTubeItemStatus.PROBING
        self._callbacks.render_new_item(item)
        self._callbacks.clear_url_input()

        probe = YouTubeProbeWorker(self._fetcher, url, parent=self._parent)
        probe.probe_done.connect(lambda info, it=item: self._on_probe_done(it, info))
        probe.probe_error.connect(lambda msg, it=item: self._on_probe_error(it, msg))
        probe.finished.connect(lambda pw=probe: self._on_probe_finished(pw))
        self._probe_workers.append(probe)
        probe.start()
        self._callbacks.recompute_buttons()

    def _on_probe_done(self, item: YouTubeQueueItem, info: object) -> None:
        """Probe succeeded — classify the result and update the item."""
        if not isinstance(info, VideoInfo):  # pragma: no cover - signal guard
            self._mark_probe_error(item, "Invalid probe result.")
            return

        mineable, error, sub_mode = _classify_probe_result(info, self._config)
        if not mineable:
            item.video_info = info
            self._mark_probe_error(item, error or "Probe rejected.")
            return

        item.video_info = info
        item.video_id = info.video_id
        item.resolved_sub_mode = sub_mode
        item.error_message = None
        item.status = YouTubeItemStatus.READY
        self._callbacks.refresh_row(item)
        self._callbacks.recompute_buttons()

    def _on_probe_error(self, item: YouTubeQueueItem, message: str) -> None:
        """Probe failed — the item is unmineable."""
        self._mark_probe_error(item, message)

    def _mark_probe_error(self, item: YouTubeQueueItem, message: str) -> None:
        """Shared transition into PROBE_ERROR with consistent fields."""
        item.status = YouTubeItemStatus.PROBE_ERROR
        item.error_message = message
        self._callbacks.refresh_row(item)
        self._callbacks.recompute_buttons()

    def _on_probe_finished(self, probe: YouTubeProbeWorker) -> None:
        """Drop the probe handle once its QThread emits finished."""
        with contextlib.suppress(ValueError):
            self._probe_workers.remove(probe)

    # ------------------------------------------------------------------
    # Playlist resolve + expansion (Issue #70)
    # ------------------------------------------------------------------

    def _begin_playlist_resolve(self, url: str, url_info: YouTubeUrlInfo) -> None:
        """Spawn a flat-playlist resolve worker for *url*."""
        self._callbacks.log_info("Resolving playlist…")
        self._callbacks.clear_url_input()

        worker = YouTubePlaylistResolveWorker(
            self._fetcher,
            url,
            limit=self._config.youtube_playlist_max,
            parent=self._parent,
        )
        worker.playlist_resolved.connect(
            lambda pl, u=url, ui=url_info, g=self._playlist_generation: self._on_playlist_resolved(u, ui, pl, g)
        )
        worker.playlist_error.connect(self._on_playlist_resolve_error)
        worker.finished.connect(self._on_playlist_resolve_finished)
        self._playlist_resolve_worker = worker
        worker.start()
        self._callbacks.recompute_buttons()

    def _on_playlist_resolve_error(self, message: str) -> None:
        """Resolve failed — log it; the finished slot handles state cleanup."""
        self._callbacks.log_error(f"Playlist resolve failed: {message}")

    def _on_playlist_resolve_finished(self) -> None:
        """Drop the resolve handle once its QThread emits finished."""
        self._playlist_resolve_worker = None
        self._callbacks.recompute_buttons()

    def _on_playlist_resolved(
        self,
        original_url: str,
        url_info: YouTubeUrlInfo,
        pl: object,
        generation: int,
    ) -> None:
        """Resolve succeeded — confirm with the user, then expand or fall back."""
        if generation != self._playlist_generation:
            return  # User hit Clear while resolving — drop the late result.
        if not isinstance(pl, PlaylistInfo):  # pragma: no cover - signal guard
            return

        cap = self._config.youtube_playlist_max
        # Over-cap contract from YouTubeFetcherService.probe_playlist: the
        # fetcher returns up to cap+1 entries untruncated, and total_count is
        # the authoritative size when yt-dlp reports it.
        over_cap = len(pl.entries) > cap or (pl.total_count is not None and pl.total_count > cap)
        entries = pl.entries[:cap]

        choice = self._ask_playlist_choice(url_info, pl, cap, over_cap)
        if choice == "single":
            self._add_single_url(original_url)
        elif choice == "playlist":
            self._expand_playlist(entries, pl.title)
        else:
            self._callbacks.log_info("Playlist add cancelled.")

    def _ask_playlist_choice(
        self,
        url_info: YouTubeUrlInfo,
        pl: PlaylistInfo,
        cap: int,
        over_cap: bool,
    ) -> Literal["single", "playlist", "cancel"]:
        """Ask the user how to handle a resolved playlist.

        Pure playlist URLs under the cap skip the dialog entirely — the user
        already expressed intent by pasting a playlist URL. A dialog appears
        only for mixed video+playlist URLs (ambiguous intent) or over-cap
        playlists (truncation needs consent).
        """
        if url_info.kind == "playlist" and not over_cap:
            return "playlist"

        if pl.total_count is not None:
            total_text = str(pl.total_count)
        elif over_cap:
            total_text = f"more than {cap}"
        else:
            total_text = str(len(pl.entries))

        box = QMessageBox(self._parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Add Playlist")

        single_button = None
        if url_info.kind == "video_in_playlist":
            box.setText(
                f"This video is part of the playlist '{pl.title}' ({total_text} videos). "
                "Add just this video or all of them?"
            )
            single_button = box.addButton("Just this video", QMessageBox.ButtonRole.ActionRole)
            playlist_label = f"Add first {cap} of {total_text}" if over_cap else f"Add all {total_text}"
        else:
            box.setText(
                f"Playlist '{pl.title}' has {total_text} videos — more than the "
                f"configured maximum ({cap}). Add the first {cap}?"
            )
            playlist_label = f"Add first {cap}"

        playlist_button = box.addButton(playlist_label, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is playlist_button:
            return "playlist"
        if single_button is not None and clicked is single_button:
            return "single"
        return "cancel"

    def _expand_playlist(self, entries: Sequence[PlaylistEntry], playlist_title: str) -> None:
        """Add *entries* as PROBING queue rows and start the sequential probe."""
        # A single-added item keeps video_id=None until its probe completes, so
        # fall back to the URL-derived id; otherwise a video added standalone
        # (probe in flight) then again via a playlist slips the dedup and gets
        # fetched twice for the same YT:<video_id>.
        existing_ids = {i.video_id or classify_youtube_url(i.url).video_id for i in self._callbacks.queued_items()}
        existing_ids.discard(None)
        seen: set[str] = set()
        kept_entries: list[PlaylistEntry] = []
        skipped = 0
        for entry in entries:
            if entry.video_id in existing_ids or entry.video_id in seen:
                skipped += 1
                continue
            seen.add(entry.video_id)
            kept_entries.append(entry)

        if skipped:
            self._callbacks.log_warning(f"Skipped {skipped} already-queued video(s).")
        if not kept_entries:
            self._callbacks.log_info(f"No new videos to add from playlist '{playlist_title}'.")
            return

        kept_items: list[YouTubeQueueItem] = []
        for entry in kept_entries:
            item = self._callbacks.enqueue(entry.url)
            item.video_id = entry.video_id
            item.display_title = entry.title
            item.status = YouTubeItemStatus.PROBING
            self._callbacks.render_new_item(item)
            kept_items.append(item)

        # Snapshot BEFORE starting the worker — idx signals resolve against
        # this frozen list so user row-removals can't shift the mapping.
        self._playlist_probe_items = kept_items
        worker = YouTubePlaylistProbeWorker(self._fetcher, [e.url for e in kept_entries], parent=self._parent)
        worker.entry_probed.connect(self._on_playlist_entry_probed)
        worker.entry_failed.connect(self._on_playlist_entry_failed)
        worker.finished.connect(self._on_playlist_probe_finished)
        self._playlist_probe_worker = worker
        worker.start()

        self._callbacks.log_info(f"Added {len(kept_items)} videos from playlist '{playlist_title}'.")
        self._callbacks.recompute_buttons()

    def _playlist_item_at(self, idx: int) -> YouTubeQueueItem | None:
        """Map a playlist-probe ``idx`` back to a queue item.

        Resolves against the frozen ``_playlist_probe_items`` snapshot, then
        confirms the item is still queued — a row the user removed mid-probe
        is skipped (no status mutation on a detached item).
        """
        if not (0 <= idx < len(self._playlist_probe_items)):
            return None
        item = self._playlist_probe_items[idx]
        if item not in self._callbacks.queued_items():
            return None
        return item

    def _on_playlist_entry_probed(self, idx: int, info: object) -> None:
        """Entry probe succeeded — reuse the single-video probe-done logic."""
        item = self._playlist_item_at(idx)
        if item is None:
            return
        self._on_probe_done(item, info)

    def _on_playlist_entry_failed(self, idx: int, message: str) -> None:
        """Entry probe failed — reuse the single-video probe-error transition."""
        item = self._playlist_item_at(idx)
        if item is None:
            return
        self._mark_probe_error(item, message)

    def _on_playlist_probe_finished(self) -> None:
        """Drop the probe handle + snapshot once its QThread emits finished."""
        self._playlist_probe_worker = None
        self._playlist_probe_items = []
        self._callbacks.recompute_buttons()
