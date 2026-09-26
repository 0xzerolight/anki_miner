"""S3: every KnownWordDB construction names the active mining language.

In the mould of ``test_known_words_path_sites.py``: a static guard over every
construction in ``anki_miner/`` plus drivers for sites that can run headless.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import anki_miner
from anki_miner.gui.workers import deck_filter_worker

REPO_ROOT = Path(anki_miner.__file__).resolve().parent.parent

#: The six construction sites (spec S3) plus the helper's own construction.
EXPECTED_SITES = {
    "anki_miner/gui/utils/service_factory.py",
    "anki_miner/gui/main_window.py",
    "anki_miner/gui/widgets/settings_tab.py",
    "anki_miner/gui/workers/deck_filter_worker.py",
    "anki_miner/gui/widgets/_mining_tab_base.py",
    "anki_miner/services/known_word_db.py",
    # Resource bundles read the ignore list on export and add to it on import;
    # both receive the path from resolve_known_words_db_path in the GUI layer.
    "anki_miner/services/resource_bundle/export.py",
    "anki_miner/services/resource_bundle/install.py",
}


def _constructions():
    for path in (REPO_ROOT / "anki_miner").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ("KnownWordDB", "add_user_known_words"):
                yield path.relative_to(REPO_ROOT).as_posix(), node


def test_every_construction_passes_the_language():
    missing = [
        f"{path}:{node.lineno}"
        for path, node in _constructions()
        if not any(keyword.arg == "language" for keyword in node.keywords)
    ]
    assert missing == []


def test_the_sites_are_the_known_six():
    assert {path for path, _node in _constructions()} == EXPECTED_SITES
    assert sum(1 for path, _node in _constructions() if path == "anki_miner/gui/widgets/settings_tab.py") == 2


def test_deck_filter_bundle_opens_the_active_language(test_config, monkeypatch):
    from anki_miner.languages.registry import get_profile

    seen: list[dict] = []
    monkeypatch.setattr(deck_filter_worker, "KnownWordDB", lambda path, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(deck_filter_worker, "get_profile", lambda code: get_profile("ja"))
    monkeypatch.setattr("anki_miner.languages.tagger_provider.get_tagger", lambda code: None)

    deck_filter_worker._build_filter_bundle(dataclasses.replace(test_config, language="zh"), None)

    assert seen == [{"language": "zh"}]
