"""File system utilities."""

from pathlib import Path


def ensure_directory(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path to ensure exists

    Returns:
        The directory path
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    """Make a filename safe for the file system.

    Args:
        filename: Original filename

    Returns:
        Safe filename with invalid characters removed
    """
    import re

    # Remove or replace invalid filename characters
    invalid_chars = '<>:"/\\|?*'
    safe_name = filename
    for char in invalid_chars:
        safe_name = safe_name.replace(char, "_")

    # Remove control characters
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", safe_name)

    # Handle Windows reserved names
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{name}{i}" for name in ("COM", "LPT") for i in range(1, 10)}
    stem = Path(safe_name).stem.upper()
    if stem in reserved:
        safe_name = f"_{safe_name}"

    # Truncate to 255 bytes (filesystem limit)
    if len(safe_name.encode("utf-8")) > 255:
        ext = Path(safe_name).suffix
        name = Path(safe_name).stem
        while len((name + ext).encode("utf-8")) > 255:
            name = name[:-1]
        safe_name = name + ext

    # Fallback for empty result
    if not safe_name or not safe_name.strip():
        safe_name = "unnamed"

    return safe_name
