"""Centralized constants for the GUI layer."""

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
MIN_HEIGHT_PROGRESS_WIDGET = 80

# =============================================================================
# FILE FILTERS FOR DIALOGS
# =============================================================================
VIDEO_FILE_FILTER = "Video Files (*.mp4 *.mkv *.avi *.m4v *.mov);;All Files (*)"
SUBTITLE_FILE_FILTER = "Subtitle Files (*.ass *.srt *.ssa);;All Files (*)"

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
LOG_MAX_LINES = 1000  # Rotation trigger: rotate once line count reaches this
LOG_ROTATION_THRESHOLD = 800  # Lines kept after rotation (trigger is LOG_MAX_LINES)
