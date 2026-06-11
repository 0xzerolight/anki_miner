"""Print floor pins for deps the smoke job re-installs at minimum version.

Reads ``project.dependencies`` from ``pyproject.toml`` and emits ``name==X.Y.Z``
specs for the deps in ``FRAGILE`` based on each one's ``>=`` lower bound. The
CI smoke-min-deps job consumes this so that the workflow file does not
hardcode floor versions that can drift out of sync with ``pyproject.toml``.

Fail-closed: if any ``FRAGILE`` name produces no pin (e.g. its specifier
switched from ``>=`` to ``~=``/``==``, or it was renamed/removed), exit nonzero
rather than silently dropping it — otherwise smoke-min-deps would test that dep
at its latest version instead of its floor, masking exactly what it guards.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

FRAGILE = ("yt-dlp", "psutil")


def floor_pins(deps: list[str]) -> list[str]:
    """Return ``name==X.Y.Z`` pins for every ``FRAGILE`` dep in ``deps``.

    Raises ``SystemExit`` (nonzero) if any ``FRAGILE`` name has no ``>=`` floor
    in ``deps``, since smoke-min-deps would otherwise silently fall back to the
    latest version for that dep.
    """
    pinned: dict[str, str] = {}
    for spec in deps:
        m = re.match(r"([A-Za-z0-9_.-]+)\s*>=\s*([0-9][\w.]*)", spec)
        if m and m.group(1) in FRAGILE:
            pinned[m.group(1)] = f"{m.group(1)}=={m.group(2)}"

    missing = [name for name in FRAGILE if name not in pinned]
    if missing:
        raise SystemExit(
            "extract_floor_pins: no >= floor found for FRAGILE dep(s): "
            + ", ".join(missing)
            + " — update FRAGILE or the pyproject specifier."
        )
    return [pinned[name] for name in FRAGILE]


def main() -> None:
    deps = tomllib.loads(Path("pyproject.toml").read_text())["project"]["dependencies"]
    print(" ".join(floor_pins(deps)))


if __name__ == "__main__":
    main()
