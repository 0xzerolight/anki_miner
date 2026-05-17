"""Payload describing one card to be created via AnkiConnect."""

from dataclasses import dataclass

from anki_miner.models.media import MediaData
from anki_miner.models.word import TokenizedWord


@dataclass(frozen=True)
class CardPayload:
    """One card's data ready for AnkiConnect submission."""

    word: TokenizedWord
    media: MediaData
    definition: str
    extra_fields: dict[str, str] | None = None
