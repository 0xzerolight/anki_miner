"""Centralized constants for the GUI layer."""

from anki_miner.utils.file_pairing import DEFAULT_SUBTITLE_PRIORITY

# =============================================================================
# WINDOW CONFIGURATION
# =============================================================================
WINDOW_MIN_WIDTH = 1024
WINDOW_MIN_HEIGHT = 768
WINDOW_DEFAULT_WIDTH = 1280
WINDOW_DEFAULT_HEIGHT = 800

# =============================================================================
# APPLICATION INFO
# =============================================================================
APP_NAME = "Anki Miner"

# =============================================================================
# WIDGET MINIMUM HEIGHTS
# =============================================================================
MIN_HEIGHT_LOG_WIDGET = 200

# =============================================================================
# FILE FILTERS FOR DIALOGS
# =============================================================================
VIDEO_FILE_FILTER = "Video Files (*.mp4 *.mkv *.avi *.m4v *.mov);;All Files (*)"
#: Derived, not restated: the picker offers exactly what FilePairMatcher will
#: pair, so a format added to the priority tuple can never be missing here.
SUBTITLE_FILE_FILTER = (
    f"Subtitle Files ({' '.join(f'*{ext}' for ext in sorted(DEFAULT_SUBTITLE_PRIORITY))});;All Files (*)"
)

# =============================================================================
# SUBTITLE OFFSET RANGE
# =============================================================================
SUBTITLE_OFFSET_MIN = -300.0
SUBTITLE_OFFSET_MAX = 300.0

# =============================================================================
# PATH DISPLAY
# =============================================================================
PATH_MAX_DISPLAY_LENGTH = 60

# =============================================================================
# LOG WIDGET CONFIGURATION
# =============================================================================
LOG_MAX_LINES = 1000  # Retained entries; exceeding it trims back to the threshold
LOG_ROTATION_THRESHOLD = 800  # Entries kept after a trim (trigger is LOG_MAX_LINES)
