"""Load an existing Anki deck's notes as per-card reading units (kind="deck").

Issue #131: premade subs2srs / movies2anki / asbplayer decks carry a subtitle
line, its audio clip and a picture per card, but no target word. Each note
becomes one :class:`ReadingUnit`, mined like a subtitle cue; the card's own
clip and picture ride along as files in Anki's ``collection.media`` and are
uploaded again, under content-addressed names, for the cards mined from it. The
source deck is only ever read.

Config-free and Qt-free like the other loaders: warnings are plain strings.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePath

from anki_miner.models.reading import DeckFieldMap

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
    # Ties on share break by name: a sentence-named field beats a word-named
    # one (Core 2k/6k: Vocabulary-Audio before Sentence-Audio; Lapis:
    # ExpressionAudio before SentenceAudio), and a "video" field loses.
    scored = [
        (_share(n, samples, fits), "sentence" in n.casefold(), "video" not in n.casefold(), -i, n)
        for i, n in enumerate(names)
    ]
    best = max(scored, default=None)
    return best[-1] if best is not None and best[0] >= SAMPLE_SHARE else ""


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
