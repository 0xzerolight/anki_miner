"""Interface protocols for Anki Miner."""

from .dictionary_provider import DictionaryProvider
from .presenter import PresenterProtocol
from .progress import ProgressCallback

__all__ = ["DictionaryProvider", "PresenterProtocol", "ProgressCallback"]
