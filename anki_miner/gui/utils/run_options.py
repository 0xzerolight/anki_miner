"""Persist a workflow screen's inline run options into the saved config.

An inline run option is a control the user sets on the screen that runs the
work -- "Review words before mining", Deck Builder's word-selection mode --
rather than in Settings. They are ordinary preferences, so they belong in
``AnkiMinerConfig`` and travel with a settings profile or an export, exactly
as the Condense and Download tabs' options already do.

The screen declares ``run_options_changed`` itself and mixes this in for the
behaviour. The signal cannot live here: a ``pyqtSignal`` on a mixin that is
not a ``QObject`` never binds, and making the mixin a ``QObject`` reintroduces
the metaclass conflict documented in ``gui/presenters/gui_presenter.py``.
``TaskPublisherMixin`` is the same shape for the same reason.

Two guards, both load-bearing:

* :meth:`RunOptionsMixin.seeding` suppresses the persist path while widgets are
  being written to programmatically. Saving fires ``config_refreshed``, which
  re-enters every screen's ``update_config``, which re-seeds -- without this the
  first toggle loops. It is re-entrant because a re-seed legitimately nests
  (Card Backfill re-gates its checkboxes from inside its own seed).
* :meth:`RunOptionsMixin.persist_run_options` returns early when nothing
  actually moved, so an unrelated save (a theme toggle, a settings commit)
  cannot chain into another.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import pyqtBoundSignal

from anki_miner.config import AnkiMinerConfig


class RunOptionsMixin:
    """Seed-guarded persistence for a screen's inline run options.

    The host must own a ``config: AnkiMinerConfig`` attribute and declare
    ``run_options_changed = pyqtSignal(object)``.
    """

    # What the host provides. Bare annotations only -- no runtime class
    # attribute, so the host's real pyqtSignal is untouched. Same convention as
    # ``MiningTabBase.config``.
    config: AnkiMinerConfig
    run_options_changed: pyqtBoundSignal

    #: Depth of the active seed guard. An int, not a bool: a re-seed nests.
    _seed_depth: int = 0

    @property
    def _seeding(self) -> bool:
        """Whether a programmatic seed is in progress."""
        return self._seed_depth > 0

    @contextmanager
    def seeding(self) -> Iterator[None]:
        """Suppress persistence while widgets are written to programmatically."""
        self._seed_depth += 1
        try:
            yield
        finally:
            self._seed_depth -= 1

    def persist_run_options(self, **fields: Any) -> bool:
        """Fold edited options into the config and emit; report whether it did.

        Returns ``False`` without emitting while seeding, and when every value
        already matches -- so a slot wired to several widgets can be connected
        once per widget without multiplying saves.

        ``fields`` is ``Any`` rather than ``object`` because the config's fields
        are heterogeneous: a ``**dict[str, object]`` splat fails every one of
        ``replace``'s per-field overloads.
        """
        if self._seeding:
            return False
        config = self.config
        new_config = replace(config, **fields)
        if new_config == config:
            return False
        self.config = new_config
        self.run_options_changed.emit(new_config)
        return True
