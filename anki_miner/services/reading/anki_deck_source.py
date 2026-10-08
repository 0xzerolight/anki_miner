"""Load an existing Anki deck's notes as per-card reading units (kind="deck").

Issue #131: premade subs2srs / movies2anki / asbplayer decks carry a subtitle
line, its audio clip and a picture per card, but no target word. Each note
becomes one :class:`ReadingUnit`, mined like a subtitle cue; the card's own
clip and picture ride along as files in Anki's ``collection.media`` and are
uploaded again, under content-addressed names, for the cards mined from it. The
source deck is only ever read.

Config-free and uses no Qt APIs, like the other loaders: warnings are plain strings.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePath
from typing import Protocol

from anki_miner.exceptions import AnkiConnectionError, SetupError, raise_if_cancelled
from anki_miner.models.reading import DeckFieldMap, ImageRef, ReadingDocument, ReadingSourceRef, ReadingUnit

# The escaping Deck Filter's deck query uses (quotes, backslash, and the `_`/`*`
# wildcards a subs2srs name like "Show_01" is full of), imported from its
# definition as card_backfiller does.
from anki_miner.services.card_restyler import _escape_note_type as _escape_anki_search
from anki_miner.services.reading._util import READING_CANCELLED
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.text_utils import clean_subtitle_text

logger = logging.getLogger(__name__)

#: Extensions a ``[sound:]`` ref must have to count as sentence audio. The
#: subs2srs Video field is also a ``[sound:]`` ref (.avi/.mp4) and must not win;
#: .webm stays in because asbplayer records audio-only .webm unless told to
#: re-encode to mp3 (a video .webm field loses on name, see _best_media_field).
AUDIO_EXTS = frozenset({".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".flac", ".spx", ".webm", ".mka"})

#: A field qualifies for a role when at least this share of sampled notes fit it.
SAMPLE_SHARE = 0.5

# Name hints, best first. "sentence" outranks "expression" because note types
# built for word cards (Lapis, Kiku) call their WORD field Expression.
_SENTENCE_HINTS = ("sentence", "expression", "subs1", "line", "text")
_TRANSLATION_HINTS = ("meaning", "translation", "english", "subs2", "native")

_SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
_IMG_SRC_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_BREAK_RE = re.compile(r"<br\s*/?>|</?(?:div|p)\b[^>]*>", re.IGNORECASE)
# Ruby readings are not part of the line: <ruby>漢字<rt>かんじ</rt></ruby> -> 漢字.
_RUBY_TEXT_RE = re.compile(r"<(rt|rp)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def sound_filename(value: str) -> str | None:
    """Return the first ``[sound:x]`` in ``value`` whose extension is audio."""
    for match in _SOUND_RE.finditer(value):
        name = html.unescape(match.group(1)).strip()
        if PurePath(name).suffix.lower() in AUDIO_EXTS:
            return name
    return None


def image_filename(value: str) -> str | None:
    """Return the ``src`` of the first ``<img>`` in ``value``."""
    match = _IMG_SRC_RE.search(value)
    if match is None:
        return None
    raw = next(group for group in match.groups() if group is not None)
    return html.unescape(raw).strip() or None


def field_text(value: str) -> str:
    """Return a field's text: breaks kept as newlines, markup and sound refs dropped."""
    text = _SOUND_RE.sub("", value)
    text = _RUBY_TEXT_RE.sub("", text)
    text = _BREAK_RE.sub("\n", text)
    text = html.unescape(_TAG_RE.sub("", text)).replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _hint_rank(name: str, hints: tuple[str, ...]) -> int:
    folded = name.casefold().replace(" ", "")
    return next((rank for rank, hint in enumerate(hints) if hint in folded), len(hints))


def _share(name: str, samples: Sequence[Mapping[str, str]], fits: Callable[[str], bool]) -> float:
    if not samples:
        return 0.0
    return sum(1 for sample in samples if fits(sample.get(name, ""))) / len(samples)


def _best_media_field(names: Sequence[str], samples: Sequence[Mapping[str, str]], fits: Callable[[str], bool]) -> str:
    # Among the fields that qualify, the name decides before coverage: a
    # "video" field loses (a subs2srs Video clip on every note must not beat
    # an Audio field one note short), and a sentence-named field beats a
    # word-named one (Core 2k/6k: Vocabulary-Audio before Sentence-Audio;
    # Lapis: ExpressionAudio before SentenceAudio).
    scored = [
        (
            (share := _share(n, samples, fits)) >= SAMPLE_SHARE,
            "video" not in n.casefold(),
            "sentence" in n.casefold(),
            share,
            -i,
            n,
        )
        for i, n in enumerate(names)
    ]
    best = max(scored, default=None)
    return best[-1] if best is not None and best[0] else ""


def suggest_field_map(
    field_names: Sequence[str],
    samples: Sequence[Mapping[str, str]],
    *,
    contains_target_script: Callable[[str], bool],
) -> DeckFieldMap:
    """Guess which fields hold the line, its audio, picture and translation.

    Media fields are judged on content. The sentence is the best-named field
    whose text is in the mining language on at least half the sampled notes
    (name first, because a marker like ``ep01_0001`` is "Latin text" too), and
    the translation is picked by name only. ``sentence`` is "" when no field
    qualifies; the user picks it.
    """
    audio = _best_media_field(field_names, samples, lambda v: sound_filename(v) is not None)
    picture = _best_media_field(
        [n for n in field_names if n != audio], samples, lambda v: image_filename(v) is not None
    )
    rest = [n for n in field_names if n not in (audio, picture)]

    def is_line(value: str) -> bool:
        text = field_text(value)
        return bool(text) and contains_target_script(text)

    ranked = [
        (_hint_rank(n, _SENTENCE_HINTS), -share, i, n)
        for i, n in enumerate(rest)
        if (share := _share(n, samples, is_line)) >= SAMPLE_SHARE
    ]
    sentence = min(ranked)[3] if ranked else ""
    hinted = [
        ("sentence" not in n.casefold(), _hint_rank(n, _TRANSLATION_HINTS), i, n)
        for i, n in enumerate(rest)
        if n != sentence and _hint_rank(n, _TRANSLATION_HINTS) < len(_TRANSLATION_HINTS)
    ]
    translation = min(hinted)[3] if hinted else ""
    return DeckFieldMap(sentence=sentence, audio=audio, picture=picture, translation=translation)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_NOTES_CHUNK = 500  # every notesInfo caller chunks at 500 (deck_filter, card_backfiller)


class DeckNoteReader(Protocol):
    """The three AnkiConnect reads a deck load needs (AnkiService satisfies it)."""

    def find_notes(self, query: str) -> list[int]: ...
    def notes_info(self, note_ids: list[int]) -> list[dict]: ...
    def media_dir_path(self) -> Path | None: ...


def _value(fields: Mapping[str, object], name: str) -> str:
    entry = fields.get(name) if name else None
    value = entry.get("value") if isinstance(entry, dict) else None
    return value if isinstance(value, str) else ""


def _media_file(media_dir: Path, name: str | None) -> Path | None:
    """Resolve ``name`` inside ``media_dir``; refuse anything that is not a bare file name.

    A shared deck is untrusted input: a separator, ``..`` or a Windows drive
    prefix (``C:x.mp3`` makes the join drop ``media_dir``) would read a file
    from elsewhere and upload it into the collection. Anki never writes ``:``
    into a media file name, so refusing it costs nothing.
    """
    if not name or name in {".", ".."} or any(ch in name for ch in "/\\:"):
        return None
    path = media_dir / name
    return path if path.is_file() else None


def load(
    ref: ReadingSourceRef,
    anki: DeckNoteReader,
    *,
    cancel_check: Callable[[], bool] | None = None,
    normalize: Callable[[str], str] | None = None,
    has_target_script: Callable[[str], bool] | None = None,
) -> ReadingDocument:
    """Read every note of ``ref.title`` (subdecks included) into one unit per card.

    Lines are cleaned with the run parser's ``normalize`` / bilingual-cue gate,
    as the subtitle loader does. Media that cannot be found stays off that card
    and is counted into one warning; an unreadable media folder is one warning.

    Raises:
        SetupError: no note of the deck has text in the sentence field.
        AnkiConnectionError: Anki is unreachable.
    """
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    fields = ref.deck_fields
    assert ref.kind == "deck" and fields is not None  # ReadingSourceRef.__post_init__ enforces it
    deck = ref.title
    note_ids = sorted(anki.find_notes(f'deck:"{_escape_anki_search(deck)}"'))
    wants_media = bool(fields.audio or fields.picture)
    media_dir: Path | None = None
    if wants_media:
        try:
            media_dir = anki.media_dir_path()
        except AnkiConnectionError as exc:
            # findNotes just worked, so this is AnkiConnect refusing the action
            # (an old add-on); degrade like an unreadable folder, never fail.
            logger.warning("Anki media dir lookup failed: error=%s", type(exc).__name__)
    media_ok = media_dir is not None and media_dir.is_dir()
    warnings: list[str] = []
    if wants_media and not media_ok:
        where = f" ({media_dir})" if media_dir is not None else ""
        warnings.append(
            f"Anki's media folder{where} can't be read from here, so cards from "
            f"'{deck}' get no audio or picture from the deck."
        )
    units: list[ReadingUnit] = []
    missing = 0
    for start in range(0, len(note_ids), _NOTES_CHUNK):
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        for offset, note in enumerate(anki.notes_info(note_ids[start : start + _NOTES_CHUNK])):
            values = note.get("fields")
            if not isinstance(values, dict):
                continue
            text = clean_subtitle_text(
                field_text(_value(values, fields.sentence)),
                normalize=normalize,
                has_target_script=has_target_script,
            )
            if not text:
                continue
            audio_ref: Path | None = None
            picture: Path | None = None
            if media_ok:
                assert media_dir is not None
                if fields.audio:
                    raw_audio = _value(values, fields.audio)
                    audio_ref = _media_file(media_dir, sound_filename(raw_audio))
                    # A ref that is missing on disk OR not audio (a video clip
                    # in a hand-picked field) is counted, never dropped silently.
                    missing += audio_ref is None and "[sound:" in raw_audio
                if fields.picture:
                    raw_picture = _value(values, fields.picture)
                    picture = _media_file(media_dir, image_filename(raw_picture))
                    missing += picture is None and "<img" in raw_picture.casefold()
            units.append(
                ReadingUnit(
                    text=text,
                    index=len(units),
                    location_label=f"#{start + offset + 1}",
                    image_ref=ImageRef(picture) if picture is not None else None,
                    audio_ref=audio_ref,
                    translation=" ".join(field_text(_value(values, fields.translation)).splitlines()),
                )
            )
    if not units:
        raise SetupError(f"No sentences found in the '{fields.sentence}' field of deck '{deck}'.")
    if missing:
        warnings.append(
            f"{missing} audio or picture file(s) used by deck '{deck}' are missing from "
            "Anki's media folder or are not audio; those cards go without them."
        )
    log_summary(
        logger,
        "Anki deck load",
        deck=deck,
        notes=len(note_ids),
        units=len(units),
        missing_media=missing,
        media_dir_readable=media_ok,
    )
    return ReadingDocument(title=deck, kind="deck", series=deck, episode=deck, units=units, warnings=warnings)
