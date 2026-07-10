"""Interface protocols for Anki Miner."""

from .dictionary_provider import DictionaryProvider
from .expression_audio import ExpressionAudioFetcher
from .presenter import PresenterProtocol
from .progress import ProgressCallback
from .sentence_audio import SentenceAudioFetcher

__all__ = [
    "DictionaryProvider",
    "ExpressionAudioFetcher",
    "PresenterProtocol",
    "ProgressCallback",
    "SentenceAudioFetcher",
]
