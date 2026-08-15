"""Structural parity gate for the translated READMEs under ``i18n/``.

Fails when README.md changes without the translations being refreshed, or
when a translation drops a section, table row, link, image or code block.
Fix a red run with one of:

    python scripts/readme_i18n.py nav      # nav block drifted
    python scripts/readme_i18n.py stamp    # English edit consciously accepted
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "readme_i18n.py"
_spec = importlib.util.spec_from_file_location("readme_i18n", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ri = importlib.util.module_from_spec(_spec)
sys.modules["readme_i18n"] = ri
_spec.loader.exec_module(ri)


def test_every_ui_language_has_a_readme() -> None:
    missing = [code for code in ri.codes() if not ri.translation_path(code).exists()]
    assert missing == [], f"no README for UI languages: {missing}"


def test_translated_readmes_are_structurally_faithful_and_current() -> None:
    problems = ri.check()
    assert problems == [], "\n".join(["README translation drift:", *problems])
