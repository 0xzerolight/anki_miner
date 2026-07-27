"""Utilities for computing sensible start directories for file dialogs."""

from __future__ import annotations

from pathlib import Path


def resolve_start_dir(
    current: str | None,
    *,
    file_mode: bool,
    remembered_dir: Path | str | None = None,
    default_dir: Path | str | None = None,
) -> str:
    """File-dialog start dir; never returns '/'.

    Priority:
    1. dir implied by `current` (parent for files, the dir itself for folders),
       walking up to the first existing ancestor (filesystem root is never
       returned — if the walk reaches root, falls through to the next level);
    2. else `remembered_dir` — the folder last ACCEPTED for this workflow and
       role (D7) — if it is still a directory;
    3. else `default_dir` if it exists;
    4. else Path.home().

    The remembered folder sits *below* the field because what is in the field
    is what the user is working on right now; it sits *above* the configured
    default because a default is a starting guess and the remembered folder is
    evidence.

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
    if remembered_dir is not None:
        # A remembered folder can have been deleted, unmounted or renamed since
        # it was chosen; it is skipped, never walked up from — the ancestor of a
        # folder the user picked once is not somewhere they asked to be.
        r = Path(remembered_dir).expanduser()
        if r.is_dir():
            return str(r)
    if default_dir is not None:
        d = Path(default_dir).expanduser()
        if d.exists():
            return str(d)
    return str(Path.home())
