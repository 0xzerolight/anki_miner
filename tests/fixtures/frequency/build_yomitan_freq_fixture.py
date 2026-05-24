"""Build a minimal Yomitan-format frequency zip for tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


def build_yomitan_freq_zip(
    zip_path: Path,
    *,
    title: str = "Test Freq",
    revision: str = "v1",
    format_version: int | None = 3,
    frequency_mode: str | None = None,
    meta_banks: list[list[Any]] | None = None,
) -> Path:
    """Create a Yomitan freq zip at ``zip_path``. Returns ``zip_path``.

    Each item in ``meta_banks`` is one bank file. Entries are ``[term, mode, data]``
    triples passed through verbatim — callers control mode and data shape.
    Pass ``format_version=None`` to omit the ``format`` key entirely (used to
    test the importer's missing-format rejection path).
    """
    if meta_banks is None:
        meta_banks = [
            [
                ["猫", "freq", 100],
                ["犬", "freq", 200],
            ]
        ]

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {
        "title": title,
        "revision": revision,
    }
    if format_version is not None:
        index["format"] = format_version
    if frequency_mode is not None:
        index["frequencyMode"] = frequency_mode

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.json", json.dumps(index))
        for i, bank in enumerate(meta_banks, 1):
            zf.writestr(f"term_meta_bank_{i}.json", json.dumps(bank))
    return zip_path
