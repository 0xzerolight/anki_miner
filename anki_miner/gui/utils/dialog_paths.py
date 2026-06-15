"""Utilities for computing sensible start directories for file dialogs."""

from __future__ import annotations

from pathlib import Path


def resolve_start_dir(
    current: str | None,
    *,
    file_mode: bool,
    default_dir: Path | str | None = None,
) -> str:
    """File-dialog start dir; never returns '/'.

    Priority:
    1. dir implied by `current` (parent for files, the dir itself for folders),
       walking up to the first existing ancestor (filesystem root is never
       returned — if the walk reaches root, falls through to default_dir);
    2. else `default_dir` if it exists;
    3. else Path.home().

    Absolute paths are expected; relative paths resolve against cwd.
    In folder mode, if `current` is a file its parent dir is used.
    """
    if current and current.strip():
        p = Path(current).expanduser()
        cand = p if (not file_mode and p.is_dir()) else p.parent
        while cand != cand.parent and not cand.exists():
            cand = cand.parent
        if cand.exists() and cand != cand.parent:
            return str(cand)
    if default_dir is not None:
        d = Path(default_dir).expanduser()
        if d.exists():
            return str(d)
    return str(Path.home())
