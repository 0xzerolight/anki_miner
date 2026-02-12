"""Text processing utilities."""

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
