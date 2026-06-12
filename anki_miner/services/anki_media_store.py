"""Media upload pipeline for AnkiConnect: batched ``storeMediaFile`` actions.

Split out of ``AnkiService`` so the chunk-budget logic and dict-media src
resolution are unit-testable without HTTP mocks. ``AnkiMediaStore`` owns the
shared upload path used by both card media (screenshots/audio) and
dictionary-bundled assets referenced from definition/glossary HTML:
``_build_store_media_action`` → ``_chunk_media_actions`` (count + byte
budget) → ``_store_media_chunk`` (per-file fallback on a failed ``multi``
POST).
"""

import base64
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import CardPayload
from anki_miner.services._ankiconnect import post_action, post_multi
from anki_miner.services.dictionary.yomitan_renderer import DICT_MEDIA_CLASS

logger = logging.getLogger(__name__)

# `<img>` tags emitted by yomitan_renderer for dictionary-bundled assets carry
# `class="anki-miner-dict-media"`. Capture the whole tag, then pull `src` out —
# attribute order in the rendered HTML is fixed but a single regex makes the
# scan tolerant of future renderer reshuffles.
_DICT_MEDIA_IMG_RE = re.compile(
    rf'<img\b[^>]*class="[^"]*\b{re.escape(DICT_MEDIA_CLASS)}\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)

# Media uploads are base64-heavy; a smaller chunk than the 100-note addNotes
# batch keeps individual request payloads manageable.
_MEDIA_BATCH_CHUNK = 50
# AnkiConnect resets the connection on very large `multi` request bodies (one
# 50-file chunk of YouTube clips can hit ~7-8 MB of base64), surfacing as a
# requests ConnectionError that reads "Is Anki running?" even though it is.
# Bound each `multi` POST by cumulative base64 size as well as action count so a
# chunk of large files flushes early instead of tripping the reset (Issue: media
# files not stored on big batches).
_MEDIA_BATCH_MAX_BYTES = 4 * 1024 * 1024


def _extract_dict_media_srcs(definition_html: str) -> list[str]:
    """Return every dict-media `src` referenced in a definition HTML blob."""
    if not definition_html:
        return []
    out: list[str] = []
    for tag in _DICT_MEDIA_IMG_RE.findall(definition_html):
        m = _IMG_SRC_RE.search(tag)
        if m:
            out.append(m.group(1))
    return out


def _resolve_dict_media_path(src: str, dicts_root: Path) -> Path | None:
    """Map an Anki-side dict-media filename back to the file on disk.

    The renderer formats src as ``<dict_id>__<flattened-basename>``. dict_id is
    a lowercase-ASCII slug with hyphens (importer guarantees no double-`__`),
    so we split on the first ``__``. The resolved path must stay inside the
    dicts_root tree.
    """
    if "__" not in src:
        return None
    dict_id, _, safe = src.partition("__")
    if not dict_id or not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    try:
        root_resolved = dicts_root.resolve()
        candidate = (dicts_root / dict_id / "media" / safe).resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _chunk_media_actions(items: list[tuple[str, dict]]) -> Iterator[list[tuple[str, dict]]]:
    """Yield (filename, action) sublists bounded by count and base64 byte budget.

    Flushes the current chunk before adding an action that would push it past
    ``_MEDIA_BATCH_CHUNK`` actions or ``_MEDIA_BATCH_MAX_BYTES`` of base64
    data. A single action larger than the byte budget still ships alone.
    """
    chunk: list[tuple[str, dict]] = []
    chunk_bytes = 0
    for filename, action in items:
        action_bytes = len(action["params"].get("data", ""))
        if chunk and (len(chunk) >= _MEDIA_BATCH_CHUNK or chunk_bytes + action_bytes > _MEDIA_BATCH_MAX_BYTES):
            yield chunk
            chunk = []
            chunk_bytes = 0
        chunk.append((filename, action))
        chunk_bytes += action_bytes
    if chunk:
        yield chunk


def _build_store_media_action(filename: str, src_path: Path) -> dict | None:
    """Build a ``storeMediaFile`` action dict for use in a ``multi`` envelope.

    Returns ``None`` and logs a warning if the file cannot be read.
    """
    try:
        with open(src_path, "rb") as f:
            data_base64 = base64.b64encode(f.read()).decode("utf-8")
    except OSError as e:
        logger.warning(f"Failed to read media file {filename}: {e}")
        return None
    return {
        "action": "storeMediaFile",
        "version": 6,
        "params": {"filename": filename, "data": data_base64},
    }


class AnkiMediaStore:
    """Stores card media and dict-bundled assets in Anki via AnkiConnect."""

    def __init__(self, config: AnkiMinerConfig):
        self.config = config
        # Number of media files (screenshots/audio) that could not be stored
        # in Anki during the last store_batch call. Mirrored onto
        # AnkiService.last_media_store_failures so the pipeline can warn the
        # user when cards land with empty media fields.
        self.last_store_failures: int = 0
        # Per-store-lifetime cache of dict-media filenames already shipped to
        # AnkiConnect this run. Avoids re-uploading the same accent SVG once
        # per card across a 5000-word batch.
        self._dict_media_uploaded: set[str] = set()

    def store_batch(self, word_data_list: list[CardPayload]) -> set[str]:
        """Store all media files in Anki collection via batched ``multi`` POSTs.

        Collects all readable (filename, base64-data) pairs, deduplicates by
        filename, then sends them in chunks bounded by both ``_MEDIA_BATCH_CHUNK``
        actions and ``_MEDIA_BATCH_MAX_BYTES`` of cumulative base64 payload per
        ``multi`` call.  Files that cannot be read (OSError) are logged and
        skipped at build time.  If a chunk's ``multi`` POST fails with a transport
        error (AnkiConnect resets the connection on oversized bodies), the chunk
        is retried one file at a time via single ``storeMediaFile`` POSTs.
        Per-sub-action AnkiConnect errors (sub-result with an ``"error"`` key)
        exclude that filename from the returned set.

        Sets ``self.last_store_failures`` to the count of files that could
        not be stored so callers can surface it to the user instead of silently
        creating cards with empty media fields.

        Args:
            word_data_list: List of CardPayload objects whose media should be uploaded

        Returns:
            Set of filenames that were successfully stored
        """
        # Build (filename → action) mapping, deduped by filename (first writer
        # wins; duplicates point at the same file, so content is identical
        # either way). Dedup BEFORE the read+encode so a file shared by N
        # payloads — e.g. audiobook cover art on every card — is read and
        # base64-encoded once, not N times.
        actions_by_filename: dict[str, dict] = {}
        for item in word_data_list:
            media = item.media
            for filename, src_path in [
                (media.screenshot_filename, media.screenshot_path),
                (media.audio_filename, media.audio_path),
                (media.expression_audio_filename, media.expression_audio_path),
            ]:
                if not filename or not src_path or not src_path.exists():
                    continue
                if filename in actions_by_filename:
                    continue
                action = _build_store_media_action(filename, src_path)
                if action is not None:
                    actions_by_filename[filename] = action

        if not actions_by_filename:
            self.last_store_failures = 0
            return set()

        stored: set[str] = set()
        for chunk in _chunk_media_actions(list(actions_by_filename.items())):
            stored |= self._store_media_chunk(chunk)

        self.last_store_failures = len(actions_by_filename) - len(stored)
        return stored

    def upload_dict_media(self, word_data_list: list[CardPayload]) -> None:
        """Batch-upload all dict-media assets referenced across the whole card batch.

        Scans each item's ``definition`` and ``extra_fields["glossary"]`` for
        ``<img class="anki-miner-dict-media" src="…">`` tags, collects the union
        of un-uploaded srcs, resolves each to a file path, and ships them through
        the same pipeline as card screenshots/audio: ``_build_store_media_action``
        → ``_chunk_media_actions`` (count + byte budget) → ``_store_media_chunk``
        (per-file fallback on a failed ``multi`` POST).

        Missing-on-disk srcs are logged as warnings and added to
        ``_dict_media_uploaded`` so they are not retried on every card (identical
        to the old per-card behavior). Otherwise a src is cached only after a
        confirmed successful store — a failed upload stays uncached so the next
        batch retries it.
        """
        # Collect un-uploaded srcs across the whole batch (ordered, deduped).
        seen: set[str] = set()
        all_srcs: list[str] = []
        for item in word_data_list:
            for html_field in (
                item.definition,
                item.extra_fields.get("glossary") if item.extra_fields else None,
            ):
                if not isinstance(html_field, str):
                    continue
                for src in _extract_dict_media_srcs(html_field):
                    if src not in self._dict_media_uploaded and src not in seen:
                        seen.add(src)
                        all_srcs.append(src)

        if not all_srcs:
            return

        # Resolve each src; cache missing ones now so we don't retry.
        items: list[tuple[str, dict]] = []
        for src in all_srcs:
            file_path = _resolve_dict_media_path(src, self.config.dicts_root)
            if file_path is None:
                logger.warning("Dict media file missing on disk: %s", src)
                # Cache anyway so we don't retry every card.
                self._dict_media_uploaded.add(src)
                continue
            action = _build_store_media_action(src, file_path)
            if action is not None:
                items.append((src, action))

        # Shared with the screenshot/audio path: chunks bounded by action count
        # AND base64 byte budget, per-file fallback when a multi POST trips the
        # oversized-body connection reset. _store_media_chunk returns only the
        # srcs confirmed stored, so failures stay uncached and retry next batch.
        for chunk in _chunk_media_actions(items):
            self._dict_media_uploaded |= self._store_media_chunk(chunk)

    def _store_media_chunk(self, chunk: list[tuple[str, dict]]) -> set[str]:
        """Store one chunk via ``multi``; fall back to per-file POSTs on transport failure."""
        filenames = [f for f, _ in chunk]
        actions = [a for _, a in chunk]
        try:
            sub_results = post_multi(self.config.ankiconnect_url, actions, timeout=30)
        except AnkiConnectionError as e:
            cause = e.__cause__
            logger.warning(
                "Media batch multi POST failed (%s: %s); retrying %d file(s) individually",
                type(cause).__name__ if cause is not None else type(e).__name__,
                e,
                len(actions),
            )
            return self._store_media_files_individually(chunk)

        if len(sub_results) != len(actions):
            logger.warning(
                "post_multi returned %d results for %d actions; some files may be silently skipped",
                len(sub_results),
                len(actions),
            )
        stored: set[str] = set()
        for filename, sub_result in zip(filenames, sub_results, strict=False):
            if not (isinstance(sub_result, dict) and sub_result.get("error")):
                stored.add(filename)
        return stored

    def _store_media_files_individually(self, chunk: list[tuple[str, dict]]) -> set[str]:
        """Per-file ``storeMediaFile`` fallback (tiny bodies) for a failed-multi chunk.

        This is the pre-batching upload path: each file goes in its own small POST,
        which avoids the oversized-body connection reset that breaks the ``multi``
        envelope. Files AnkiConnect still rejects are logged and excluded.
        """
        stored: set[str] = set()
        for filename, action in chunk:
            try:
                post_action(
                    self.config.ankiconnect_url,
                    "storeMediaFile",
                    params=action["params"],
                    timeout=30,
                )
                stored.add(filename)
            except AnkiConnectionError as e:
                logger.warning("Failed to store media file %s individually: %s", filename, e)
        return stored
