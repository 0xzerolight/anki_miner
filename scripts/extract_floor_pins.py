"""Print floor pins for deps the smoke job re-installs at minimum version.

Reads ``project.dependencies`` from ``pyproject.toml`` and emits ``name==X.Y.Z``
specs for the deps in ``FRAGILE`` based on each one's ``>=`` lower bound. The
CI smoke-min-deps job consumes this so that the workflow file does not
hardcode floor versions that can drift out of sync with ``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

FRAGILE = ("yt-dlp", "psutil")


def main() -> None:
    deps = tomllib.loads(Path("pyproject.toml").read_text())["project"]["dependencies"]
    pins: list[str] = []
    for spec in deps:
        m = re.match(r"([A-Za-z0-9_.-]+)\s*>=\s*([0-9][\w.]*)", spec)
        if m and m.group(1) in FRAGILE:
            pins.append(f"{m.group(1)}=={m.group(2)}")
    print(" ".join(pins))


if __name__ == "__main__":
    main()
