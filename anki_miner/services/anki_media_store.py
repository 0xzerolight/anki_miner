"""Media upload pipeline for AnkiConnect: batched ``storeMediaFile`` actions.

Split out of ``AnkiService`` so the chunk-budget logic and dict-media src
resolution are unit-testable without HTTP mocks. ``AnkiMediaStore`` owns the
shared upload path used by both card media (screenshots/audio) and
dictionary-bundled assets referenced from definition/glossary HTML:
``_build_store_media_action`` → ``_chunk_media_actions`` (count + byte
budget) → ``_store_media_chunk`` (per-file fallback on a failed ``multi``
POST).

``store_batch`` streams: filenames are deduplicated first (cheap, no I/O),
then base64 encoding happens lazily inside ``_stream_encode_chunks`` as each
chunk is assembled, so only one chunk's worth of encoded data (~4 MB) is
resident in memory at a time.  ``_chunk_media_actions`` is kept for the
``upload_dict_media`` path which pre-builds actions before chunking.
"""

import base64
import hashlib
import html
import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import CardPayload, MediaData
from anki_miner.services._ankiconnect import post_action, post_multi
from anki_miner.services.dictionary.yomitan_renderer import DICT_MEDIA_CLASS

logger = logging.getLogger(__name__)

# (filename_attr, path_attr) pairs on MediaData that carry an uploadable file.
# Iterated by store_batch to collect sources and to propagate content-hashed
# stored names back onto the payload before note building.
_MEDIA_FIELD_ATTRS = (
    ("screenshot_filename", "screenshot_path"),
    ("audio_filename", "audio_path"),
    ("expression_audio_filename", "expression_audio_path"),
)

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
    """Return every dict-media `src` referenced in a definition HTML blob.

    The renderer HTML-escapes the src (``escape(img_src, quote=True)``), so a
    basename carrying ``&``/``"``/``<`` appears here as ``&amp;`` etc. Unescape it
    back to the on-disk / browser-requested name so downstream disk resolution
    (``_resolve_dict_media_path``), the ``storeMediaFile`` name, and the upload
    cache all key on the same unescaped string (else the file never matches on
    disk and re-misses forever).
    """
    if not definition_html:
        return []
    out: list[str] = []
    for tag in _DICT_MEDIA_IMG_RE.findall(definition_html):
        m = _IMG_SRC_RE.search(tag)
        if m:
            out.append(html.unescape(m.group(1)))
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


def _stream_encode_chunks(
    items: Iterable[tuple[str, Path]],
) -> Iterator[list[tuple[str, str, dict]]]:
    """Yield chunks of ``(orig_filename, stored_filename, action)`` triples.

    ``orig_filename`` is the pre-hash name the payload carries; ``stored_filename``
    is the content-addressed name actually sent to AnkiConnect (and set as
    ``action.params.filename``), so the caller can propagate it back onto the
    payload after a confirmed store (7.5).

    Unlike ``_chunk_media_actions`` (which requires actions to be pre-built),
    this generator encodes each file only when it is about to be added to the
    current chunk.  File size is estimated via ``stat()`` (base64 expansion
    ratio ≈ 4/3) before encoding so the byte budget can be checked without
    reading file contents twice.  Only one chunk's worth of encoded data is
    resident in memory at a time; the caller discards each chunk after its POST.

    Files that cannot be read (``OSError``) or stat'd are logged as warnings
    and skipped — consistent with ``_build_store_media_action`` behaviour.
    """
    chunk: list[tuple[str, str, dict]] = []
    chunk_bytes = 0
    for filename, src_path in items:
        # Estimate encoded size from file size to decide whether to flush first.
        try:
            raw_size = src_path.stat().st_size
        except OSError as e:
            logger.warning("Failed to stat media file %s: %s", filename, e)
            continue
        # base64 encodes 3 raw bytes → 4 ASCII chars; round up to next 4-byte
        # boundary.  The +3 before integer division handles the padding.
        estimated_bytes = ((raw_size + 2) // 3) * 4

        if chunk and (len(chunk) >= _MEDIA_BATCH_CHUNK or chunk_bytes + estimated_bytes > _MEDIA_BATCH_MAX_BYTES):
            yield chunk
            chunk = []
            chunk_bytes = 0

        # Encode now — just before it enters the chunk. Card media is
        # content-hashed so cross-episode same-name/different-bytes clips no
        # longer overwrite each other in Anki's media collection.
        action = _build_store_media_action(filename, src_path, content_hash=True)
        if action is None:
            # _build_store_media_action already logged the warning.
            continue
        stored_name = action["params"]["filename"]
        chunk.append((filename, stored_name, action))
        chunk_bytes += len(action["params"].get("data", ""))

    if chunk:
        yield chunk


def _content_addressed_name(filename: str, content: bytes) -> str:
    """Return ``{stem}_{sha1[:12]}{ext}`` for a content-addressed Anki media name.

    Ported concept from Yomitan ``ext/js/data/anki-util.js``
    ``mediaFileNameHashOrTimestamp`` / ``generateAnkiNoteMediaFileName`` (upstream
    commit e2ed450): a SHA-1 of the file bytes replaces the collision-prone
    ``{word}_{timestamp}`` name so same content re-mines to one deterministic name
    while different content (OP/ED karaoke at the same offset across episodes) can
    no longer overwrite a prior card's clip via ``storeMediaFile``.
    """
    digest = hashlib.sha1(content).hexdigest()[:12]  # noqa: S324 - content address, not security
    p = Path(filename)
    return f"{p.stem}_{digest}{p.suffix}"


def _build_store_media_action(filename: str, src_path: Path, content_hash: bool = False) -> dict | None:
    """Build a ``storeMediaFile`` action dict for use in a ``multi`` envelope.

    Returns ``None`` and logs a warning if the file cannot be read. When
    ``content_hash`` is True the stored ``params.filename`` is content-addressed
    (``{stem}_{sha1[:12]}{ext}``) so distinct bytes never collide on one Anki
    media name (7.5). The dict-media path passes False so the src name the
    rendered ``<img>`` references is preserved.
    """
    try:
        with open(src_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        logger.warning(f"Failed to read media file {filename}: {e}")
        return None
    stored_name = _content_addressed_name(filename, raw) if content_hash else filename
    data_base64 = base64.b64encode(raw).decode("utf-8")
    return {
        "action": "storeMediaFile",
        "version": 6,
        "params": {"filename": stored_name, "data": data_base64},
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

        Deduplicates filenames first (cheap, no I/O), then streams base64
        encoding lazily via ``_stream_encode_chunks``: only one chunk's worth
        of encoded data (~4 MB) is resident in memory at a time.  Each chunk
        is POSTed and its encoded data dropped before the next chunk is
        assembled.  Files that cannot be read (OSError) are logged and skipped
        at encode time.  If a chunk's ``multi`` POST fails with a transport
        error (AnkiConnect resets the connection on oversized bodies), the chunk
        is retried one file at a time via single ``storeMediaFile`` POSTs.
        Per-sub-action AnkiConnect errors (sub-result with an ``"error"`` key)
        exclude that filename from the returned set.

        Card media is content-hashed at encode time (7.5): the stored name is
        ``{stem}_{sha1[:12]}{ext}``, and the final name AnkiConnect confirms is
        propagated back onto every ``MediaData`` field that referenced the
        pre-hash name — so ``build_note`` (which only references filenames in the
        returned set) points cards at the actually-stored name.

        Sets ``self.last_store_failures`` to the count of files that could
        not be stored so callers can surface it to the user instead of silently
        creating cards with empty media fields.  Files whose source path was set
        but vanished from disk before upload are also counted as failures.

        Args:
            word_data_list: List of CardPayload objects whose media should be uploaded

        Returns:
            Set of the final (content-hashed / AnkiConnect-confirmed) filenames
            that were successfully stored
        """
        # Dedup by filename first (cheap, no encoding).  First writer wins —
        # duplicate filenames point at the same content, so whichever path we
        # pick encodes the same bytes.
        paths_by_filename: dict[str, Path] = {}
        # Track filenames that had a path on disk at pipeline time but vanished
        # before we could upload them (e.g. user deleted audio_cache/jpod101/
        # mid-run — the documented retry procedure for miss markers). These are
        # distinct from legitimately absent media (filename or src_path is
        # None/empty — silently skipped). Vanished files can't be uploaded but
        # they should count as failures so the caller can warn the user about
        # cards landing with empty fields.
        vanished: set[str] = set()
        # Every (MediaData, filename_attr) that referenced each pre-hash name, so
        # the confirmed stored name can be written back onto all of them.
        refs: dict[str, list[tuple[MediaData, str]]] = {}
        for item in word_data_list:
            media = item.media
            for fn_attr, path_attr in _MEDIA_FIELD_ATTRS:
                filename = getattr(media, fn_attr)
                src_path = getattr(media, path_attr)
                if not filename or not src_path:
                    # Legitimately absent — no media for this field, stay silent.
                    continue
                refs.setdefault(filename, []).append((media, fn_attr))
                if not src_path.exists():
                    # Had a path but the file is gone; count as a failure unless
                    # already deduped (first encounter owns the failure slot).
                    if filename not in paths_by_filename and filename not in vanished:
                        logger.warning("Media source file vanished before upload: %s", filename)
                        vanished.add(filename)
                    continue
                if filename in paths_by_filename:
                    continue
                paths_by_filename[filename] = src_path

        if not paths_by_filename:
            self.last_store_failures = len(vanished)
            return set()

        # Stream: encode lazily per chunk; only one chunk's base64 is in RAM
        # at a time.  Files that fail to encode are skipped (logged inside
        # _stream_encode_chunks) and excluded from the attempt count below.
        stored_finals: set[str] = set()
        # pre-hash name -> final stored name (content hash, or AnkiConnect's own
        # rename if it differs from what we sent).
        rename: dict[str, str] = {}
        attempted: set[str] = set()
        for chunk in _stream_encode_chunks(paths_by_filename.items()):
            attempted.update(orig for orig, _, _ in chunk)
            result_map = self._store_media_chunk([(sent, action) for _, sent, action in chunk])
            for orig, sent, _ in chunk:
                actual = result_map.get(sent)
                if actual is not None:
                    rename[orig] = actual
                    stored_finals.add(actual)

        # Propagate the final Anki-side name onto every payload that referenced
        # the pre-hash name so build_note points cards at the stored file.
        for orig, final in rename.items():
            for media, attr in refs.get(orig, ()):
                setattr(media, attr, final)

        self.last_store_failures = len(attempted) - len(rename) + len(vanished)
        return stored_finals

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
        # Dict media is cached by the src the rendered <img> references (the sent
        # name / dict key), not any AnkiConnect rename — the HTML is already
        # emitted, so the sent name is the one that must not be re-uploaded.
        for chunk in _chunk_media_actions(items):
            self._dict_media_uploaded.update(self._store_media_chunk(chunk).keys())

    def _store_media_chunk(self, chunk: list[tuple[str, dict]]) -> dict[str, str]:
        """Store one chunk via ``multi``; fall back to per-file POSTs on transport failure.

        Returns ``{sent_filename: stored_filename}`` for every file AnkiConnect
        confirmed. ``storeMediaFile`` returns the name it actually stored under; we
        adopt that when it differs from the sent name (AnkiConnect may sanitize)
        so callers reference the real media name (7.5). A ``multi`` sub-result can
        be a bare filename string, a ``{"result": name, "error": null}`` wrapper,
        or (older versions / test stubs) a bare ``None`` success — all three are
        handled; only a wrapper carrying a truthy ``error`` is treated as failure.
        """
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
        stored: dict[str, str] = {}
        for filename, sub_result in zip(filenames, sub_results, strict=False):
            if isinstance(sub_result, dict):
                if sub_result.get("error"):
                    continue
                returned = sub_result.get("result")
                actual = returned if isinstance(returned, str) and returned else filename
            elif isinstance(sub_result, str) and sub_result:
                # Bare-string form: the value IS the stored filename.
                actual = sub_result
            else:
                # None / other: a bare success under the name we sent.
                actual = filename
            stored[filename] = actual
        return stored

    def _store_media_files_individually(self, chunk: list[tuple[str, dict]]) -> dict[str, str]:
        """Per-file ``storeMediaFile`` fallback (tiny bodies) for a failed-multi chunk.

        This is the pre-batching upload path: each file goes in its own small POST,
        which avoids the oversized-body connection reset that breaks the ``multi``
        envelope. Files AnkiConnect still rejects are logged and excluded. Returns
        ``{sent_filename: stored_filename}``, adopting the name storeMediaFile
        returns when it differs from the sent name.
        """
        stored: dict[str, str] = {}
        for filename, action in chunk:
            try:
                result = post_action(
                    self.config.ankiconnect_url,
                    "storeMediaFile",
                    params=action["params"],
                    timeout=30,
                )
                stored[filename] = result if isinstance(result, str) and result else filename
            except AnkiConnectionError as e:
                logger.warning("Failed to store media file %s individually: %s", filename, e)
        return stored
