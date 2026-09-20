"""Can this machine mine Persian? (the profile's ``unavailable_reason``).

One hard requirement, and it is DATA rather than an engine: the five hazm
``.dat`` tables, which arrive as the Persian language pack. There is no pip
route to them -- hazm declares ``Requires-Python >=3.12,<3.14``, its package
``__init__`` imports nltk/flashtext/tqdm, and ``hazm/types.py`` is a SyntaxError
before 3.12 -- so the message names the download on a source install exactly as
it does in a frozen one. The port that reads the tables is in this package and
ships with every build; only the tables are missing.

Nothing here imports anything heavy: the probe is a directory check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: The pack component holding the ``.dat`` tables. A directory name, not an
#: importable module: ``find_spec`` answers None for it everywhere, which is what
#: keeps the installer from ever calling the pack satisfied by a pip install.
FA_DATA_COMPONENT = "hazm_data"

FA_PACK_HINT = "Persian mining needs the Persian data pack. Download it in Settings -> Mining Language."


def data_root() -> Path | None:
    """The directory holding the five ``.dat`` tables, or ``None``."""
    from anki_miner.services.language_pack_installer import component_path

    return component_path("fa", FA_DATA_COMPONENT)


def fa_missing_reason() -> str | None:
    """The one sentence the selector shows, or ``None`` when Persian is ready."""
    return None if data_root() is not None else FA_PACK_HINT
