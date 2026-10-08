"""Turn one frame's OCR boxes into cue text: reading order, furigana, cursor glyphs, speaker names."""

from __future__ import annotations

from collections.abc import Sequence

from anki_miner.services.video_ocr.meiki_engine import OcrBox

_ROW_OVERLAP = 0.5  # boxes share a row when they overlap vertically by this share of the shorter one
_FURIGANA_HEIGHT = 0.6  # ruby is shorter than this share of the line it annotates
_FURIGANA_GAP = 0.5  # ...and its bottom sits within this share of its own height of that line's top
_CURSOR_GLYPHS = "▼▽▶▷◆◇ 　"
_OPEN_TO_CLOSE = {"「": "」", "『": "』", "（": "）", "(": ")"}
_NAME_MAX = 10
_NOT_IN_NAME = set("「」『』（）()：:。、！？!?")
# The recogniser's labels are NFKC-folded, so Japanese ！？〜 come back as ASCII; restore them.
_FULL_WIDTH = str.maketrans({"!": "！", "?": "？", "~": "〜"})


def _height(b: OcrBox) -> int:
    return b.bbox[3] - b.bbox[1]


def _is_furigana(small: OcrBox, boxes: Sequence[OcrBox]) -> bool:
    sh = _height(small)
    for big in boxes:
        if big is small or sh >= _FURIGANA_HEIGHT * _height(big) or small.bbox[1] >= big.bbox[1]:
            continue
        if min(small.bbox[2], big.bbox[2]) <= max(small.bbox[0], big.bbox[0]):
            continue
        if abs(big.bbox[1] - small.bbox[3]) <= _FURIGANA_GAP * sh:
            return True
    return False


def _rows(boxes: Sequence[OcrBox]) -> list[str]:
    rows: list[list[OcrBox]] = []
    for b in sorted(boxes, key=lambda b: (b.bbox[1], b.bbox[0])):
        for row in rows:
            first = row[0]
            overlap = min(first.bbox[3], b.bbox[3]) - max(first.bbox[1], b.bbox[1])
            if overlap >= _ROW_OVERLAP * min(_height(first), _height(b)):
                row.append(b)
                break
        else:
            rows.append([b])
    rows.sort(key=lambda r: min(b.bbox[1] for b in r))
    return ["".join(b.text.strip() for b in sorted(r, key=lambda b: b.bbox[0])) for r in rows]


def _is_name(text: str) -> bool:
    if not 1 <= len(text) <= _NAME_MAX:
        return False
    if not any(ch.isalpha() for ch in text):  # "……" is a stammer, not a speaker
        return False
    return not any(ch.isspace() or ch in _NOT_IN_NAME or "ぁ" <= ch <= "ゟ" for ch in text)


def _strip_speaker(rows: list[str]) -> list[str]:
    if len(rows) >= 2 and _is_name(rows[0]) and rows[1][:1] in _OPEN_TO_CLOSE:
        return rows[1:]
    first = rows[0]
    for i, ch in enumerate(first):
        if ch in "：:":
            rest = first[i + 1 :].lstrip()
            if _is_name(first[:i]) and rest and not rest[0].isdigit():  # 10:00 is a time
                return [rest, *rows[1:]]
            return rows
        if ch in "「『":
            closes = "\n".join(rows).rstrip().endswith(_OPEN_TO_CLOSE[ch])
            if i > 0 and _is_name(first[:i]) and closes:
                return [first[i:], *rows[1:]]
            return rows
    return rows


def clean(boxes: Sequence[OcrBox]) -> str:
    """The dialogue in one frame's boxes, rows joined with newlines; ``""`` when there is none."""
    kept = [b for b in boxes if b.text.strip()]
    kept = [b for b in kept if not _is_furigana(b, kept)]
    rows = [r for r in _rows(kept) if r]
    if not rows:
        return ""
    rows[-1] = rows[-1].rstrip(_CURSOR_GLYPHS)
    rows = [r for r in rows if r]
    if not rows:
        return ""
    return "\n".join(_strip_speaker(rows)).translate(_FULL_WIDTH)
