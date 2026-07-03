"""Dictionary update check-and-notify (plan 9.2).

An offline-first, notify-only port of Yomitan's dictionary update flow. Given a
dictionary's stored update metadata (recorded at import from an ``index.json``
that declared ``isUpdatable``), :func:`check_for_update` fetches the remote
``indexUrl``, re-validates the distrusted remote payload, and reports whether a
newer revision is available. It never downloads or installs anything — the user
re-imports through the existing Add/Reimport flow (the one-click
download + chain-splice half is deferred, Appendix B).

All network work is invoked only behind an explicit user action (Settings →
Dictionaries → Check for updates) and runs on a ``CancellableWorker``; unit
tests inject a fake session so no real socket is opened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, cast

import requests

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.schema_validation import (
    is_valid_dictionary_index,
    validate_http_url,
)

# Bounded so a hung mirror cannot wedge the check worker. The user triggers the
# check explicitly and can cancel between dictionaries; this caps a single fetch.
DEFAULT_TIMEOUT_S = 15.0

# Matches Yomitan's ``simpleVersionTest``: one or more dot-separated integers
# ("4.7", "24.1.1.1"), but not "1.0.0-alpha".
_SIMPLE_VERSION_RE = re.compile(r"^(\d+\.)*\d+$")


@dataclass(frozen=True)
class UpdateInfo:
    """A newer revision is available for an installed dictionary."""

    current_revision: str
    latest_revision: str
    download_url: str


class _Fetcher(Protocol):
    def get(self, url: str, timeout: float) -> requests.Response: ...


# Ported verbatim from Yomitan ``compareRevisions``
# (ext/js/dictionary/dictionary-data-util.js, upstream e2ed450).
def compare_revisions(current: str, latest: str) -> bool:
    """Return True when ``latest`` is a newer revision than ``current``.

    Dot-separated integer revisions of equal arity compare part-wise as
    integers (so "2.0" < "10.0"); anything else — non-numeric revisions or a
    differing number of parts — falls back to a plain string comparison.
    """
    if not _SIMPLE_VERSION_RE.match(current) or not _SIMPLE_VERSION_RE.match(latest):
        return current < latest

    current_parts = [int(part) for part in current.split(".")]
    latest_parts = [int(part) for part in latest.split(".")]

    if len(current_parts) != len(latest_parts):
        return current < latest

    for cur, lat in zip(current_parts, latest_parts, strict=True):
        if cur != lat:
            return cur < lat

    return False


def check_for_update(
    meta: dict[str, str],
    *,
    session: _Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> UpdateInfo | None:
    """Check whether a newer revision of one dictionary is available.

    Args:
        meta: The dictionary's stored meta rows. Update fields (``is_updatable``,
            ``index_url``, ``download_url``, ``source_revision``) are only
            present when the dictionary was imported from an ``index.json`` that
            declared itself updatable with valid http(s) URLs.
        session: HTTP client with a ``get(url, timeout)`` method. Defaults to the
            ``requests`` module; tests inject a fake to avoid real sockets.
        timeout: Per-request timeout in seconds.

    Returns:
        An :class:`UpdateInfo` when the remote revision is strictly newer, else
        ``None`` — including when the dictionary is not updatable at all (in
        which case no network request is made).

    Raises:
        SetupError: The remote index is unreachable or structurally invalid.
            (Network exceptions from the session propagate to the caller.)

    Mirrors Yomitan ``DictionaryEntry.checkForUpdate``
    (ext/js/pages/settings/dictionary-controller.js, upstream e2ed450): guard on
    updatability, fetch the index, re-validate the distrusted remote payload,
    compare revisions, and fall back to the stored download URL when the remote
    omits (or supplies an untrusted) one.
    """
    if meta.get("is_updatable") != "true":
        return None
    index_url = meta.get("index_url")
    current_download = meta.get("download_url")
    if not index_url or not current_download:
        return None
    current_revision = meta.get("source_revision", "")

    fetcher: _Fetcher = session if session is not None else cast("_Fetcher", requests)
    try:
        response = fetcher.get(index_url, timeout=timeout)
        response.raise_for_status()
        index = response.json()
    except SetupError:
        raise
    except Exception as exc:  # noqa: BLE001 — any HTTP/parse failure is one error class
        raise SetupError(f"Could not fetch dictionary index from {index_url}: {exc}") from exc

    # The remote payload is distrusted: re-validate before comparing revisions.
    if not is_valid_dictionary_index(index):
        raise SetupError(f"Remote dictionary index at {index_url} is invalid")

    latest_revision = str(index["revision"]).strip()
    remote_download = index.get("downloadUrl")
    # Prefer the remote-declared download URL, but only when it is a trusted
    # http(s) URL; otherwise keep the stored one (Yomitan's ``?? currentDownloadUrl``).
    download_url = remote_download if validate_http_url(remote_download) else current_download

    if not compare_revisions(current_revision, latest_revision):
        return None

    return UpdateInfo(
        current_revision=current_revision,
        latest_revision=latest_revision,
        download_url=download_url,
    )
