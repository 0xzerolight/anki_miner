"""Text processing utilities."""

import html
import re


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
        if "\u30a1" <= ch <= "\u30f6":
            result.append(chr(ord(ch) - 0x60))
        else:
            result.append(ch)
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
    result = []
    for token in tagger(text):
        surface = token.surface
        has_kanji = any("\u4e00" <= c <= "\u9fff" for c in surface)
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
            result.append(f"{prefix}{surface}[{hiragana}]")
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
    result = []
    for token in tagger(text):
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


def wrap_target_furigana(text: str, tagger, start: int, end: int) -> str:
    """Generate furigana-annotated text with the target morpheme wrapped in ``<b>``.

    Walks fugashi tokens over ``text``, tracking a cumulative character
    cursor in the *plain* text. Each token contributes either its surface
    or a ``surface[kana]`` annotation. Tokens whose plain-text span is
    fully contained in ``[start, end)`` are emitted inside a single
    contiguous ``<b>...</b>`` run; surrounding tokens are emitted outside.

    Matches the formatting rules of :func:`generate_furigana` so the
    bolded form is interchangeable with the regular one.

    Args:
        text: Raw subtitle line text.
        tagger: A fugashi.Tagger instance.
        start: Inclusive plain-text offset of the target morpheme.
        end: Exclusive plain-text offset of the target morpheme.

    Returns:
        Furigana-annotated text with the target morpheme bolded. If the
        offsets are invalid, falls back to :func:`generate_furigana`.
    """
    if start < 0 or end <= start or end > len(text):
        return generate_furigana(text, tagger)

    pre: list[str] = []
    body: list[str] = []
    post: list[str] = []
    cursor = 0
    out_has_content = False  # Matches generate_furigana's "prefix = ' ' if result else ''" rule

    for token in tagger(text):
        surface = token.surface
        tok_start = cursor
        tok_end = cursor + len(surface)
        cursor = tok_end

        # Pick the destination buffer for this token.
        if tok_end <= start:
            bucket = pre
        elif tok_start >= end:
            bucket = post
        else:
            # Token overlaps the bold window. The mined token is a single
            # MeCab morpheme (possibly compound-merged), so it should
            # always be fully contained. Partial overlap would only happen
            # if offsets were assigned incorrectly — treat as containment
            # to keep the output well-formed.
            bucket = body

        # Build the annotated segment using the same rules as generate_furigana,
        # but with per-token HTML escaping so the surrounding <b> tags are
        # the only raw HTML in the output. Hiragana never contains
        # html-special characters, so it's left unescaped.
        has_kanji = any("一" <= c <= "鿿" for c in surface)
        escaped_surface = html.escape(surface)
        annotated = escaped_surface
        if has_kanji:
            try:
                kana = token.feature.kana
            except AttributeError:
                kana = None
            if kana:
                hiragana = katakana_to_hiragana(kana)
                if hiragana != surface:
                    prefix = " " if out_has_content else ""
                    annotated = f"{prefix}{escaped_surface}[{hiragana}]"
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


def extract_japanese_text(text: str) -> str:
    """Extract only Japanese characters from text.

    Args:
        text: Input text

    Returns:
        Text containing only Japanese characters
    """
    # Keep hiragana, katakana, kanji, and common punctuation
    japanese_chars = []
    for char in text:
        if (
            "\u3040" <= char <= "\u309f"  # Hiragana
            or "\u30a0" <= char <= "\u30ff"  # Katakana
            or "\u4e00" <= char <= "\u9fff"  # Kanji
            or char in "。、！？ー・"
        ):
            japanese_chars.append(char)
    return "".join(japanese_chars)
