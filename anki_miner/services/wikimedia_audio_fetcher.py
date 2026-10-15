"""Wikimedia Commons word-audio fetcher (the ``wikimedia`` audio kind, F-16).

Recordings of real speakers (Wiktionary uploads and Lingua Libre) through the
keyless MediaWiki API: the spec's Stage W. One ``generator=images`` query on the
en.wiktionary page titled with the word lists every file that page embeds. The
recordings among them are filtered by the mining language's
``AudioDefaults.wikimedia_prefixes`` (``Pl-``, ``LL-Q809 (pol)-``), and the
winner's MP3 transcode on Commons is what gets cached.

The name rules are pure functions, so they are tested without the network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: File types Commons serves recordings in. A page also embeds icons and photos.
_AUDIO_EXTENSIONS = frozenset({".ogg", ".oga", ".wav", ".mp3", ".flac", ".opus"})

#: The slot that makes a prefix entry a whole-name template (spec R29, nb's bot uploads).
_TERM_SLOT = "{term}"

#: An original upload URL: the Commons base, the two hash directories, the file name.
_COMMONS_ORIGINAL = re.compile(r"^(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?#]+)")


def canonical_file_name(title: str) -> str:
    """``File:pl-woda.ogg`` → ``Pl-woda.ogg``: the name as Commons stores it.

    A page links a file with whatever first letter its editor typed, and
    MediaWiki capitalises the first letter of every file name, so prefixes are
    compared against that form.
    """
    name = title.split(":", 1)[1] if ":" in title else title
    return name[:1].upper() + name[1:]


def _rank(name: str, term: str, entry: str) -> int | None:
    """0 = a recording of exactly *term*, 1 = a numbered take; None = no match under *entry*."""
    if _TERM_SLOT in entry:
        head, tail = entry.split(_TERM_SLOT, 1)
        if len(name) < len(head) + len(tail) or not (name.startswith(head) and name.endswith(tail)):
            return None
        slot = name[len(head) : len(name) - len(tail)]
        if slot == term:
            return 0
        return 1 if re.fullmatch(re.escape(term) + r"\d+", slot) else None
    stem, dot, extension = name.rpartition(".")
    if not dot or f".{extension.lower()}" not in _AUDIO_EXTENSIONS or not stem.startswith(entry):
        return None
    rest = stem[len(entry) :]
    if rest == term:
        return 0
    if re.fullmatch(re.escape(term) + r"-?\d+", rest):
        return 1
    # Lingua Libre puts the speaker between the language and the word:
    # "LL-Q809 (pol)-Olaf-tak.wav". A legacy prefix ("En-us-") never does.
    if entry.startswith("LL-") and rest.endswith(f"-{term}") and len(rest) > len(term) + 1:
        return 0
    return None


def match_commons_file(names: Sequence[str], term: str, prefixes: Sequence[str]) -> str | None:
    """The file to fetch for *term*, or None.

    The first prefix with any match wins: prefix order is the language's
    preference. Within it, the word itself beats a numbered take (``Pl-dom.ogg``
    over ``Pl-dom-2.ogg``), and a tie goes to the alphabetically first name, so
    the pick is the same on every run.
    """
    for entry in prefixes:
        ranked = sorted((rank, name) for name in names if (rank := _rank(name, term, entry)) is not None)
        if ranked:
            return ranked[0][1]
    return None


def transcoded_mp3_url(url: str) -> str | None:
    """Commons' MP3 transcode of an original upload, the URL itself for an MP3, None outside Commons.

    ``…/commons/c/c2/Sv-tak.ogg`` → ``…/commons/transcoded/c/c2/Sv-tak.ogg/Sv-tak.ogg.mp3``.
    Commons serves this transcode for its audio uploads (probed for ``.ogg``,
    ``.oga`` and ``.wav``), so every cached file is an MP3.
    """
    match = _COMMONS_ORIGINAL.match(url)
    if match is None:
        return None
    base, hashed, filename = match.groups()
    if filename.lower().endswith(".mp3"):
        return f"{base}{hashed}{filename}"
    return f"{base}transcoded/{hashed}{filename}/{filename}.mp3"
