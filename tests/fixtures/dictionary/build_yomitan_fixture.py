"""Build a minimal Yomitan-format zip for tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


def build_yomitan_zip(
    zip_path: Path,
    *,
    title: str = "Test Dict",
    revision: str = "v1",
    term_banks: list[list[Any]] | None = None,
    tag_banks: list[list[Any]] | None = None,
    format_version: int = 3,
    media_files: dict[str, bytes] | None = None,
    styles_css: str | None = None,
) -> Path:
    """Create a minimal Yomitan zip at zip_path. Returns zip_path.

    `media_files` maps zip-relative paths (e.g. ``svg-accent/x.svg``) to their
    raw bytes. Useful for testing the asset-extraction path used by
    monolingual dictionaries with bundled images.

    `styles_css`, when given, writes a root ``styles.css`` — the per-dictionary
    stylesheet Yomitan dicts ship (Issue #87).
    """
    if term_banks is None:
        term_banks = [
            [
                ["食べる", "たべる", "v1", "v1", 0, ["to eat", "to consume"], 1, ""],
                ["飲む", "のむ", "v5m", "v5m", 0, ["to drink"], 2, ""],
            ]
        ]
    if tag_banks is None:
        tag_banks = [
            [["v1", "expression", -3, "Ichidan verb", 0]],
        ]

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "index.json",
            json.dumps(
                {
                    "title": title,
                    "revision": revision,
                    "format": format_version,
                    "sequenced": True,
                }
            ),
        )
        for i, bank in enumerate(term_banks, 1):
            zf.writestr(f"term_bank_{i}.json", json.dumps(bank))
        for i, bank in enumerate(tag_banks, 1):
            zf.writestr(f"tag_bank_{i}.json", json.dumps(bank))
        if media_files:
            for rel_path, data in media_files.items():
                zf.writestr(rel_path, data)
        if styles_css is not None:
            zf.writestr("styles.css", styles_css)
    return zip_path
