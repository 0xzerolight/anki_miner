"""Interface protocols for Anki Miner."""

from .dictionary_provider import DictionaryProvider
from .expression_audio import ExpressionAudioFetcher
from .presenter import PresenterProtocol
from .progress import ProgressCallback

__all__ = ["DictionaryProvider", "ExpressionAudioFetcher", "PresenterProtocol", "ProgressCallback"]
