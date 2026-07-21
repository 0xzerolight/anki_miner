"""Text processing utilities."""

import html
import re
from collections.abc import Iterable
from typing import Any

from anki_miner.utils.furigana_distribute import distribute_furigana
from anki_miner.utils.ja_normalize import (
    is_cjk_ideograph,
    normalize_for_tokenization,
    standardize_kanji_variants,
)


def strip_subtitle_markup(text: str) -> str:
    """Strip subtitle formatting markup without any language normalization.

    Removes the three tag families that :func:`clean_subtitle_text` handles:
    ASS/SSA override blocks (``{\\...}``), the ``\\N``/``\\n`` line-break markers
    (each replaced by a space), and HTML tags (``<...>``). It deliberately does
    NOT run the MeCab-oriented Japanese normalization (halfwidth→fullwidth kana,
    NFKD folding, kanji-variant mapping) nor collapse whitespace, so the returned
    string is safe to display verbatim to the user (e.g. condensed subtitles).

    Args:
        text: Raw subtitle text with possible formatting tags.

    Returns:
        Text with formatting markup removed; whitespace untouched.
    """
    # Remove ASS/SSA style tags like {\pos(x,y)}, {\fad(100,200)}, etc.
    text = re.sub(r"\{[^}]*\}", "", text)

    # Remove line break tags
    text = re.sub(r"\\[nN]", " ", text)

    # Remove HTML tags if present
    text = re.sub(r"<[^>]+>", "", text)

    return text


def clean_subtitle_text(text: str, *, strip_annotations: bool = False) -> str:
    """Remove formatting tags, then Japanese-normalize for tokenization.

    Markup stripping runs first, then :func:`normalize_for_tokenization`
    (halfwidth katakana → fullwidth, NFC combining-mark composition, CJK-compat
    and radical NFKD folding) and the minimal kanji-variant map (𠮟 → 叱). When
    annotation stripping is enabled, physical lines stay separate through
    normalization and are stripped before whitespace is flattened. The returned
    string *is* the text MeCab tokenizes and the stored card sentence, so token
    offsets, dedup keys, and script-type filters all see one normalized form.

    Args:
        text: Raw subtitle text with possible formatting tags
        strip_annotations: Strip annotations per physical line after normalization

    Returns:
        Cleaned, normalized text without formatting tags
    """
    if strip_annotations:
        # Preserve physical lines until the gated post-normalization strip;
        # strip_subtitle_markup normally flattens ASS/SSA \N and \n to spaces.
        text = re.sub(r"\\[nN]|\r\n?", "\n", text)
    text = strip_subtitle_markup(text)

    # Preserve the pre-annotation behavior exactly when the opt-in is disabled.
    if not strip_annotations:
        text = " ".join(text.split())

    # Japanese pre-tokenization normalization (see anki_miner.utils.ja_normalize).
    text = normalize_for_tokenization(text)
    text = standardize_kanji_variants(text)

    if strip_annotations:
        text = strip_inline_annotations(text)
        text = " ".join(text.split())

    return text.strip()


# Structural subtitle-annotation stripping (Task U1). ``strip_inline_annotations``
# is the parser choke-point stripper for the largest batch-mining junk class found
# in the 816-card audit: parenthetical SFX captions, leading speaker tags, and
# inline furigana tokenized as dialogue. Handles BOTH fullwidth （） and halfwidth
# () parens; mixed nesting occurs in real data (（水篠(みずしの) 旬(しゅん)）).
_ANNOTATION_OPEN = "（("
_ANNOTATION_CLOSE = "）)"
# One *innermost* balanced paren group (content has no nested parens), either
# width. Group 1 = fullwidth content, group 2 = halfwidth content.
_INNERMOST_PAREN_GROUP_RE = re.compile(r"（([^（）()]*)）|\(([^（）()]*)\)")
# Furigana cap: a kana paren group after a kanji is furigana only when short.
# A longer kana parenthetical after a kanji word is likely a real aside, so it
# is left intact (precision over recall).
_FURIGANA_MAX_KANA = 10
_ANNOTATION_STRIP_MAX_PASSES = 32


def _is_furigana_content(content: str) -> bool:
    """True iff *content* is a non-blank run of kana + whitespace within the cap.

    Kana = hiragana/katakana plus the kana marks :func:`_is_kana_only` accepts
    (ー・ and iteration marks); interspersed whitespace is allowed
    (``みず しの``). Blank or all-whitespace content is not furigana.
    """
    if not (0 < len(content) <= _FURIGANA_MAX_KANA) or not content.strip():
        return False
    return all(_is_kana_only(ch) or ch.isspace() for ch in content)


def _strip_furigana_match(match: re.Match[str]) -> str:
    """Delete an inline-furigana paren group, keeping its preceding kanji.

    Fires only when the group sits *immediately* after a kanji and its content
    is short kana-only furigana (瀕死(ひんし) → 瀕死). Every other group is
    returned verbatim so the pass is a no-op on non-furigana parentheticals.
    """
    start = match.start()
    if start == 0 or not _is_kanji(match.string[start - 1]):
        return match.group(0)
    content = match.group(1) if match.group(1) is not None else match.group(2)
    if not _is_furigana_content(content):
        return match.group(0)
    return ""


def _match_balanced_group(text: str, start: int) -> int | None:
    """Index just past the paren group opening at ``text[start]``, or ``None``.

    Depth-counts across both paren widths (so mixed-width nesting like
    ``（水篠(みずしの)）`` matches), returning the offset one past the close that
    balances the opener. Returns ``None`` when ``text[start]`` is not an opener
    or the group is never balanced (malformed input — the caller then leaves the
    text unchanged, never throwing).
    """
    if start >= len(text) or text[start] not in _ANNOTATION_OPEN:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch in _ANNOTATION_OPEN:
            depth += 1
        elif ch in _ANNOTATION_CLOSE:
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _is_whole_line_caption(text: str) -> bool:
    """True iff *text* is solely balanced paren group(s) + whitespace.

    Whole-line SFX captions (（スマホのバイブ音）, （笑い声） （拍手）) carry no
    dialogue, so the caller returns an empty line. Any non-space character
    outside a group — or an unbalanced group — makes this ``False`` so genuine
    dialogue (and speaker-tag-plus-line cases) survives for pass 3.
    """
    i = 0
    n = len(text)
    found_group = False
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in _ANNOTATION_OPEN:
            end = _match_balanced_group(text, i)
            if end is None:
                return False
            found_group = True
            i = end
            continue
        return False
    return found_group


def strip_inline_annotations(text: str) -> str:
    """Structurally strip subtitle annotations that mine into junk cards.

    Three ordered, config-free passes, each handling both fullwidth （） and
    halfwidth () parens:

    1. **Inline furigana** — a kanji-run immediately followed by a short
       (≤10-char) kana-only paren group has that group deleted, keeping the
       kanji: ``瀕死(ひんし)`` → ``瀕死``; innermost groups resolve first so
       ``（水篠(みずしの) 旬(しゅん)）`` → ``（水篠 旬）``.
    2. **Whole-line caption** — if what remains is solely paren group(s) +
       whitespace, the whole line is an SFX caption and becomes ``""``
       (``（スマホのバイブ音）`` → ``""``).
    3. **Leading speaker tag** — any balanced paren group at the line start is
       peeled (with following whitespace), repeatedly, so
       ``（旬: 小声で）余計な…`` → ``余計な…``. Deliberately broader than names.

    Each pass applies independently to every physical line (actual newlines or
    ASS/SSA ``\\N``/``\\n`` markers), so an annotation at any physical line start
    cannot become mid-cue dialogue when whitespace is later flattened. Mid-line
    paren groups containing kanji are left untouched (conservative). Balanced-
    paren matching only:
    malformed/unbalanced parens leave the text unchanged. Pure function — no
    I/O, no config; the caller gates it.
    """
    return "\n".join(_strip_inline_annotations_line(line) for line in re.split(r"\\[nN]|\r\n?|\n", text))


def _strip_inline_annotations_line(text: str) -> str:
    """Apply the three annotation passes to one physical subtitle line."""
    # Pass 1: inline furigana. Re-run until stable so adjacent groups whose
    # kanji-adjacency only appears after an earlier deletion also resolve
    # (漢(あ)(い) → 漢). Each successful sub strictly shrinks the string, so
    # each pass shrinks the string. Cap the work so a long run of adjacent
    # groups cannot make this repeat-until-stable pass quadratic.
    for _ in range(_ANNOTATION_STRIP_MAX_PASSES):
        stripped = _INNERMOST_PAREN_GROUP_RE.sub(_strip_furigana_match, text)
        if stripped == text:
            break
        text = stripped

    # Pass 2: whole-line SFX caption.
    if _is_whole_line_caption(text):
        return ""

    # Pass 3: leading speaker/SFX tag(s).
    while True:
        lead = len(text) - len(text.lstrip())
        if lead >= len(text) or text[lead] not in _ANNOTATION_OPEN:
            break
        end = _match_balanced_group(text, lead)
        if end is None:
            break
        text = text[end:].lstrip()

    return text


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

    Delegates the ideograph test to the shared
    :func:`anki_miner.utils.ja_normalize.is_cjk_ideograph` (ported from
    Yomitan's ``CJK_IDEOGRAPH_RANGES``: Unified + Ext A–I + compatibility +
    astral), so Ext-A/compat/astral kanji (﨑, 𠮟) are recognized, not just the
    BMP Unified block. 々 (U+3005) sits below that range, so it is added
    explicitly; it is held inside the furigana bracket (時々 → 時々[ときどき],
    not 時[とき]々). Used both as the kanji-containment gate and by
    :func:`_format_furigana` to find the okurigana boundary.
    """
    return is_cjk_ideograph(char) or char == "々"


def _is_kana_only(text: str) -> bool:
    """True iff every char is kana or a kana mark (ー・, iteration marks)."""
    return bool(text) and all("ぁ" <= ch <= "ゖ" or "ァ" <= ch <= "ヺ" or ch in "ー・ゝゞヽヾ" for ch in text)


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

    Render-layer deviation from the port (2026-07 card audit F6): a kana-only
    segment whose reading is just its own fold carries no information — Yomitan's
    raw-codepoint compare brackets katakana against hiragana readings
    (``バカ[ばか]``, and ``エネルギ[えねるぎ]ー`` with an orphaned ー). Such
    segments are collapsed to plain text here, with adjacent plain segments
    merged, so ``バカ力``/``ばかりょく`` renders ``バカ 力[りょく]`` and
    ``エネルギー源``/``えねるぎーげん`` renders ``エネルギー 源[げん]``. The
    ``distribute_furigana`` port itself stays byte-faithful.

    ``reading`` is expected to already be hiragana (the callers apply
    :func:`katakana_to_hiragana`). Interior kana and rendaku now segment
    (取り引き/とりひき → ``取[と]り 引[ひ]き``); genuinely ambiguous splits (e.g.
    飼い犬/かいいぬ) fall back to whole-word bracketing inside
    :func:`distribute_furigana`. 々 stays inside its kanji group's bracket.
    """
    normalized: list[tuple[str, str]] = []
    for segment in distribute_furigana(surface, reading):
        text, seg_reading = segment.text, segment.reading
        if seg_reading and _is_kana_only(text) and katakana_to_hiragana(text) == katakana_to_hiragana(seg_reading):
            seg_reading = ""
        if normalized and not seg_reading and not normalized[-1][1]:
            normalized[-1] = (normalized[-1][0] + text, "")
        else:
            normalized.append((text, seg_reading))

    result = ""
    for text, seg_reading in normalized:
        if seg_reading:
            if result:
                result += " "
            result += f"{text}[{seg_reading}]"
        else:
            result += text
    return result


def _leads_with_bracket(rendered: str) -> bool:
    """True iff a ``_format_furigana`` render starts with a bracketed segment.

    Only then does a token-separator space serve its purpose (binding the
    leading ``[...]`` to this token's kanji instead of the previous run). A
    plain-leading render (``しっぽ 切[き]り``) must NOT get one — Anki's furigana
    filter only consumes a space directly before a ``X[...]`` group, so a space
    before plain kana renders literally on the card (audit F6: トカゲの しっぽ).
    """
    bracket = rendered.find("[")
    if bracket == -1:
        return False
    space = rendered.find(" ")
    return space == -1 or bracket < space


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
            formatted = _format_furigana(surface, hiragana)
            # Separator space only when the render leads with a bracket group —
            # a space before a plain-leading render shows literally in Anki.
            prefix = " " if result and _leads_with_bracket(formatted) else ""
            result.append(f"{prefix}{formatted}")
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
                    formatted = _format_furigana(surface, hiragana)
                    prefix = " " if out_has_content and _leads_with_bracket(formatted) else ""
                    annotated = f"{prefix}{html.escape(formatted)}"
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
