"""Interface protocols for Anki Miner."""

from .presenter import PresenterProtocol
from .progress import ProgressCallback

__all__ = ["PresenterProtocol", "ProgressCallback"]
