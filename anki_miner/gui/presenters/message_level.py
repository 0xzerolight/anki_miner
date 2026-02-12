"""Message level enum for presenter signals."""

from enum import Enum


class MessageLevel(Enum):
    """Severity level for messages displayed to the user."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
