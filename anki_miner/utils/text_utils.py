"""Text processing utilities."""

import html
import re
from collections.abc import Iterable
from typing import Any

from anki_miner.utils.furigana_distribute import distribute_furigana


def clean_subtitle_text(text: str) -> str:
    """Remove formatting tags and clean up subtitle text.

    Args:
        text: Raw subtitle text with possible formatting tags

    Returns:
        Cleaned text without formatting tags
    """
    # Remove ASS/SSA style tags like {\pos(x,y)}, {\fad(100,200)}, etc.
    text = re.sub(r"\{[^}]*\}", "", text)

    # Remove line break tags
    text = re.sub(r"\\[nN]", " ", text)

    # Remove HTML tags if present
    text = re.sub(r"<[^>]+>", "", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text.strip()


def katakana_to_hiragana(text: str) -> str:
    """Convert katakana characters to hiragana.

    Args:
        text: Text potentially containing katakana

    Returns:
        Text with katakana converted to hiragana
    """
    result = []
    for ch in text:
        if "ァ" <= ch <= "ヶ":
            result.append(chr(ord(ch) - 0x60))
        else:
            result.append(ch)
    return "".join(result)


def hiragana_to_katakana(text: str) -> str:
    """Convert hiragana characters to katakana.

    Inverse of :func:`katakana_to_hiragana`.  The prolonged-sound mark ``ー``
    and any already-katakana characters pass through unchanged, so the mapping
    round-trips losslessly for plain kana readings.

    Args:
        text: Text potentially containing hiragana

    Returns:
        Text with hiragana converted to katakana
    """
    result = []
    for ch in text:
        if "ぁ" <= ch <= "ゖ":
            result.append(chr(ord(ch) + 0x60))
        else:
            result.append(ch)
    return "".join(result)


def has_katakana(text: str) -> bool:
    """Return True if *text* contains any katakana character."""
    return any("ァ" <= ch <= "ヶ" for ch in text)


def _is_kanji(char: str) -> bool:
    """True iff *char* is a CJK ideograph or the iteration mark 々.

    々 (U+3005) sits below the CJK block (U+4E00–U+9FFF), so it must be added
    explicitly; it is held inside the furigana bracket (時々 → 時々[ときどき],
    not 時[とき]々). Used both as the kanji-containment gate and by
    :func:`_format_furigana` to find the okurigana boundary.
    """
    return "一" <= char <= "鿿" or char == "々"


def _format_furigana(surface: str, reading: str) -> str:
    """Anki furigana for one morpheme, distributed per kanji group.

    Delegates to :func:`anki_miner.utils.furigana_distribute.distribute_furigana`
    (a port of Yomitan's ``distributeFurigana``) to split ``reading`` across the
    kanji of ``surface``, then renders each segment in Anki ``kanji[reading]``
    bracket form. A separator space is inserted before a bracketed segment when
    output already exists, so its reading binds to that kanji alone — Anki's
    furigana filter attaches ``[...]`` to the preceding space-delimited run, so
    ``入り口``/``いりぐち`` must render as ``入[い]り 口[ぐち]``, not
    ``入[い]り口[ぐち]`` (which would put ぐち over り口). This matches Yomitan's
    ``anki-template-renderer.js`` ``_furiganaPlain`` helper.

    ``reading`` is expected to already be hiragana (the callers apply
    :func:`katakana_to_hiragana`). Interior kana and rendaku now segment
    (取り引き/とりひき → ``取[と]り 引[ひ]き``); genuinely ambiguous splits (e.g.
    飼い犬/かいいぬ) fall back to whole-word bracketing inside
    :func:`distribute_furigana`. 々 stays inside its kanji group's bracket.
    """
    result = ""
    for segment in distribute_furigana(surface, reading):
        if segment.reading:
            if result:
                result += " "
            result += f"{segment.text}[{segment.reading}]"
        else:
            result += segment.text
    return result


def generate_furigana_from_tokens(tokens: Iterable[Any]) -> str:
    """Generate furigana-annotated text from an already-parsed token iterable.

    Iterates ``tokens`` and adds bracketed readings to kanji-containing tokens
    using the standard Anki furigana format: ``kanji[reading]``.

    Args:
        tokens: Iterable of duck-typed MeCab tokens.  Each token must expose
            ``.surface`` (str) and optionally ``.feature.kana`` (str or None).
            Compatible with real ``fugashi`` tokens and ``_SyntheticToken``.

    Returns:
        Furigana-annotated string, e.g. ``"王国[おうこく]です。"``.
    """
    result = []
    for token in tokens:
        surface = token.surface
        has_kanji = any(_is_kanji(c) for c in surface)
        if not has_kanji:
            result.append(surface)
            continue
        try:
            kana = token.feature.kana
            if not kana:
                result.append(surface)
                continue
        except AttributeError:
            result.append(surface)
            continue
        hiragana = katakana_to_hiragana(kana)
        if hiragana == surface:
            result.append(surface)
        else:
            # Add space separator before furigana only if preceded by another token
            prefix = " " if result else ""
            result.append(f"{prefix}{_format_furigana(surface, hiragana)}")
    return "".join(result)


def generate_furigana(text: str, tagger) -> str:
    """Generate furigana-annotated text using MeCab tokenization.

    Tokenizes the text and adds bracketed readings to kanji-containing tokens.
    Uses the standard Anki furigana format: kanji[reading].

    Args:
        text: Japanese text to annotate
        tagger: A fugashi.Tagger instance

    Returns:
        Furigana-annotated string, e.g. "王国[おうこく]です。"
    """
    return generate_furigana_from_tokens(tagger(text))


def generate_reading_from_tokens(tokens: Iterable[Any]) -> str:
    """Generate plain-kana reading from an already-parsed token iterable.

    Concatenates each token's kana feature (converted to hiragana) without
    bracket annotations or kanji surface forms. Tokens without a usable kana
    feature fall back to the surface form so punctuation and unknown tokens
    pass through unchanged.

    Args:
        tokens: Iterable of duck-typed MeCab tokens.  Each token must expose
            ``.surface`` (str) and optionally ``.feature.kana`` (str or None).
            Compatible with real ``fugashi`` tokens and ``_SyntheticToken``.

    Returns:
        Plain hiragana reading, e.g. ``"おうこくです。"`` for ``"王国です。"``.
    """
    result = []
    for token in tokens:
        surface = token.surface
        try:
            kana = token.feature.kana
        except AttributeError:
            kana = None
        if kana:
            result.append(katakana_to_hiragana(kana))
        else:
            result.append(surface)
    return "".join(result)


def generate_reading(text: str, tagger) -> str:
    """Generate plain-kana reading of text (Yomitan ``{reading}`` style).

    Walks MeCab tokens and concatenates each token's kana feature (converted
    to hiragana) without bracket annotations or kanji surface forms. Tokens
    without a usable kana feature fall back to the surface form so punctuation
    and unknown tokens pass through unchanged.

    Args:
        text: Japanese text to read.
        tagger: A fugashi.Tagger instance.

    Returns:
        Plain hiragana reading, e.g. ``"おうこくです。"`` for ``"王国です。"``.
    """
    return generate_reading_from_tokens(tagger(text))


def wrap_target_plain(sentence: str, start: int, end: int) -> str:
    """HTML-escape the sentence in three slices and wrap ``[start:end)`` in ``<b>``.

    The bold tag itself must not be HTML-escaped, so we slice the raw
    string first and escape each piece individually before joining.

    Args:
        sentence: Raw subtitle line text (post regex-filter, pre escape).
        start: Inclusive character offset of the target morpheme.
        end: Exclusive character offset of the target morpheme.

    Returns:
        Escaped sentence with ``<b>...</b>`` around the target morpheme.
        If ``start``/``end`` are out of range or empty span, falls back
        to plain escape.
    """
    if start < 0 or end <= start or end > len(sentence):
        return html.escape(sentence)
    prefix = html.escape(sentence[:start])
    body = html.escape(sentence[start:end])
    suffix = html.escape(sentence[end:])
    return f"{prefix}<b>{body}</b>{suffix}"


def wrap_target_furigana_from_tokens(text: str, tokens: Iterable[Any], start: int, end: int) -> str:
    """Generate furigana-annotated text with the target morpheme bolded, from pre-parsed tokens.

    Iterates ``tokens`` and locates each token's char span via
    :py:meth:`str.find` from a running cursor — MeCab silently drops
    whitespace from the token stream, so naive ``cursor += len(surface)``
    walking drifts and misaligns the bold window when ``text`` contains
    spaces (Issue #31). Each token contributes either its surface or a
    ``surface[kana]`` annotation. Tokens whose raw-text span is fully
    contained in ``[start, end)`` are emitted inside a single contiguous
    ``<b>...</b>`` run; surrounding tokens are emitted outside.

    Matches the formatting rules of :func:`generate_furigana_from_tokens` so
    the bolded form is interchangeable with the regular one.

    Args:
        text: Raw subtitle line text.  Required for ``str.find``-based cursor
            offset tracking; the token loop iterates ``tokens``, not ``text``.
        tokens: Iterable of duck-typed MeCab tokens.  Each token must expose
            ``.surface`` (str) and optionally ``.feature.kana`` (str or None).
            Compatible with real ``fugashi`` tokens and ``_SyntheticToken``.
            Must be re-iterable (e.g. a ``list``) when the fallback path is
            possible, since the invalid-offset branch re-uses the same object.
        start: Inclusive raw-text offset of the target morpheme.
        end: Exclusive raw-text offset of the target morpheme.

    Returns:
        Furigana-annotated text with the target morpheme bolded. If the
        offsets are invalid, falls back to :func:`generate_furigana_from_tokens`.
    """
    if start < 0 or end <= start or end > len(text):
        return generate_furigana_from_tokens(tokens)

    pre: list[str] = []
    body: list[str] = []
    post: list[str] = []
    cursor = 0
    out_has_content = False  # Matches generate_furigana's "prefix = ' ' if result else ''" rule

    for token in tokens:
        surface = token.surface
        # Issue #31: locate the token's actual position in ``text`` rather
        # than concatenating surface lengths, so whitespace between tokens
        # doesn't desync the bold window.
        idx = text.find(surface, cursor)
        if idx == -1:
            # Defensive: should not happen for unmodified MeCab surfaces.
            # Keep cursor where it was so we never roll backwards.
            idx = cursor
        tok_start = idx
        tok_end = tok_start + len(surface)
        cursor = tok_end

        # Pick the destination buffer for this token.
        if tok_end <= start:
            bucket = pre
        elif tok_start >= end:
            bucket = post
        else:
            # Token overlaps the bold window. The window covers the mined
            # morpheme plus (for verbs/adjectives) its trailing auxiliary
            # tokens — highlight_end is raw-token-boundary aligned, so
            # every overlapping token is fully contained and the body may
            # legitimately hold several tokens (蒔い + た). Partial overlap
            # would only happen if offsets were assigned incorrectly —
            # treat as containment to keep the output well-formed.
            bucket = body

        # Build the annotated segment using the same rules as generate_furigana,
        # but with per-token HTML escaping so the surrounding <b> tags are
        # the only raw HTML in the output. Escaping the whole post-split string
        # equals the old escape-then-bracket: readings are pure kana (no
        # &<>") and the surface is escaped either way, so no double/under-escape.
        has_kanji = any(_is_kanji(c) for c in surface)
        annotated = html.escape(surface)
        if has_kanji:
            try:
                kana = token.feature.kana
            except AttributeError:
                kana = None
            if kana:
                hiragana = katakana_to_hiragana(kana)
                if hiragana != surface:
                    prefix = " " if out_has_content else ""
                    annotated = f"{prefix}{html.escape(_format_furigana(surface, hiragana))}"
        bucket.append(annotated)
        if annotated:
            out_has_content = True

    pre_s = "".join(pre)
    body_s = "".join(body)
    post_s = "".join(post)
    if not body_s:
        # Defensive: no tokens fell in the bold range. Return the
        # unbolded concatenation so we never emit an empty <b></b>.
        return pre_s + post_s
    # The annotation rule prepends a separator space to kanji tokens
    # that follow earlier output. If the bold body starts with that
    # separator, move it outside the <b> tag so the bold envelops only
    # the morpheme itself, not its preceding whitespace.
    if body_s.startswith(" "):
        pre_s += " "
        body_s = body_s[1:]
    return f"{pre_s}<b>{body_s}</b>{post_s}"


def wrap_target_furigana(text: str, tagger, start: int, end: int) -> str:
    """Generate furigana-annotated text with the target morpheme wrapped in ``<b>``.

    Walks fugashi tokens over ``text`` and locates each token's char span
    via :py:meth:`str.find` from a running cursor — MeCab silently drops
    whitespace from the token stream, so naive ``cursor += len(surface)``
    walking drifts and misaligns the bold window when ``text`` contains
    spaces (Issue #31, parallel to the Issue #20 fix in
    ``subtitle_parser.py``). Each token contributes either its surface
    or a ``surface[kana]`` annotation. Tokens whose raw-text span is
    fully contained in ``[start, end)`` are emitted inside a single
    contiguous ``<b>...</b>`` run; surrounding tokens are emitted outside.

    Matches the formatting rules of :func:`generate_furigana` so the
    bolded form is interchangeable with the regular one.

    Args:
        text: Raw subtitle line text.
        tagger: A fugashi.Tagger instance.
        start: Inclusive raw-text offset of the target morpheme.
        end: Exclusive raw-text offset of the target morpheme.

    Returns:
        Furigana-annotated text with the target morpheme bolded. If the
        offsets are invalid, falls back to :func:`generate_furigana`.
    """
    return wrap_target_furigana_from_tokens(text, tagger(text), start, end)


def is_hiragana_only(text: str) -> bool:
    """Return True iff ``text`` is non-empty and every character is hiragana.

    Hiragana block is U+3040–U+309F. Empty strings and any text containing a
    kanji, katakana, digit, romaji, or punctuation character return False (so
    such words are kept by the script-type filter).
    """
    return bool(text) and all("぀" <= char <= "ゟ" for char in text)


def _is_katakana_char(char: str) -> bool:
    """True iff ``char`` is fullwidth or halfwidth katakana.

    Fullwidth block U+30A0–U+30FF includes the prolonged sound mark ー (U+30FC)
    and middle dot ・ (U+30FB). Halfwidth block U+FF66–U+FF9F covers the
    halfwidth katakana letters, the halfwidth prolonged mark ｰ (U+FF70) and the
    halfwidth voiced/semi-voiced sound marks ﾞ ﾟ (U+FF9E–U+FF9F), so a loanword
    typed in halfwidth such as ｺｰﾋﾞｰ counts as katakana too (Issue #57 review).
    """
    return "゠" <= char <= "ヿ" or "ｦ" <= char <= "ﾟ"


def is_katakana_only(text: str) -> bool:
    """Return True iff ``text`` is non-empty and every character is katakana.

    Counts both fullwidth (U+30A0–U+30FF) and halfwidth (U+FF66–U+FF9F)
    katakana, so コーヒー, ロボット・X and the halfwidth ｺｰﾋﾞｰ all qualify.
    Empty strings or any non-katakana character return False.
    """
    return bool(text) and all(_is_katakana_char(char) for char in text)
