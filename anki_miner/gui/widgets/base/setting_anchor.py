"""Addressable anchors for individual settings (decision D11).

Settings search has to name one logical control, jump to its panel, scroll it
into view, focus it and highlight it. An anchor is that address.

Two rules shape the design; both exist because the obvious shortcuts are wrong.

* **Stable ids never come from translated text.** An id is the owning surface's
  ``ANCHOR_NAMESPACE`` plus either the panel attribute the widget is bound to or
  an explicit name given at registration. Reordering a panel, or running the app
  in Japanese, cannot move an id. Search results, the four chain anchors D13
  must preserve, and System Health's Fix deep links all address settings by id.
* **Search text is resolved lazily.** :meth:`SettingAnchor.search_text` reads the
  live widgets on every call. An index built at the end of ``SettingsTab``
  construction — after ``app.py`` installs the Qt translators — therefore sees
  translated labels. Snapshotting the strings at registration, or building the
  index from module-level constants at import time, would hand non-English users
  an index of English.

Anchors are per-surface, not global: a module-level registry would leak between
the many ``SettingsTab`` instances a test session builds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from PyQt6.QtWidgets import QWidget

#: Returns the strings a search index should match an anchor on. Called at index
#: time, never at registration time — see the module docstring.
SettingTextProvider = Callable[[], tuple[str, ...]]


@dataclass(frozen=True)
class SettingAnchor:
    """One logical setting, addressable by a stable id.

    ``widget`` is the logical control: the thing to scroll into view and
    highlight. ``focus_target`` narrows keyboard focus to the real input when
    the logical control is a container (a combo beside its Refresh button, a
    chain list beside its reorder buttons).
    """

    stable_id: str
    widget: QWidget
    text_provider: SettingTextProvider
    focus_target: QWidget | None = None

    @property
    def scroll_widget(self) -> QWidget:
        """Widget to pass to ``QScrollArea.ensureWidgetVisible``."""
        return self.widget

    @property
    def highlight_widget(self) -> QWidget:
        """Widget that carries the transient search-hit property."""
        return self.widget

    @property
    def focus_widget(self) -> QWidget:
        """Widget that takes keyboard focus after a jump."""
        return self.focus_target if self.focus_target is not None else self.widget

    def search_text(self) -> tuple[str, ...]:
        """Resolve the searchable strings against the live widgets.

        Deduplicated, trimmed, and stripped of the trailing colon
        :meth:`FormPanel._create_field_label` appends. Never cached: see the
        module docstring on why the translator may not be installed yet when the
        anchor is created.
        """
        resolved: dict[str, None] = {}
        for raw in self.text_provider():
            text = raw.strip().rstrip(":").strip()
            if text:
                resolved.setdefault(text, None)
        return tuple(resolved)


class SettingAnchorHost:
    """Owns the setting anchors registered on one settings surface.

    Mixed in *before* the Qt base class (``class FormPanel(SettingAnchorHost,
    QFrame)``). It defines no ``__init__`` and allocates its storage on first
    use, so ``super().__init__(...)`` still lands on the Qt base and subclasses
    need no cooperative construction.
    """

    #: Prefix for every id this surface registers. Empty means "not a settings
    #: surface": nothing is registered and nothing is required. Panels reachable
    #: from Settings must set it, or the anchor-coverage test fails them.
    ANCHOR_NAMESPACE: ClassVar[str] = ""

    def register_setting(
        self,
        name: str,
        widget: QWidget,
        text_provider: SettingTextProvider,
        *,
        focus: QWidget | None = None,
    ) -> SettingAnchor:
        """Register one logical setting under ``ANCHOR_NAMESPACE``.

        Raises:
            ValueError: if ``name`` is empty or its id is already taken.
        """
        if not name:
            raise ValueError("a setting anchor needs a name")
        namespace = self.ANCHOR_NAMESPACE
        stable_id = f"{namespace}.{name}" if namespace else name
        anchors = self._setting_anchor_list()
        if any(existing.stable_id == stable_id for existing in anchors):
            raise ValueError(f"duplicate setting anchor id: {stable_id}")
        anchor = SettingAnchor(
            stable_id=stable_id,
            widget=widget,
            text_provider=text_provider,
            focus_target=focus,
        )
        anchors.append(anchor)
        return anchor

    def ignore_setting_widget(self, widget: QWidget, reason: str) -> None:
        """Record that ``widget`` is infrastructure, not a setting.

        The reason is mandatory: an unexplained exemption is indistinguishable
        from the oversight the coverage test exists to catch.
        """
        if not reason:
            raise ValueError("an ignored settings widget needs a reason")
        self._setting_ignore_map()[widget] = reason

    def setting_anchors(self) -> tuple[SettingAnchor, ...]:
        """Every anchor registered on this surface, in registration order."""
        return tuple(self._setting_anchor_list())

    def setting_ignore_reasons(self) -> Mapping[QWidget, str]:
        """Widgets deliberately excluded from anchoring, keyed to their reason."""
        return dict(self._setting_ignore_map())

    # ------------------------------------------------------------------
    # Lazily allocated storage
    # ------------------------------------------------------------------

    def _setting_anchor_list(self) -> list[SettingAnchor]:
        try:
            return self.__anchors
        except AttributeError:
            self.__anchors: list[SettingAnchor] = []
            return self.__anchors

    def _setting_ignore_map(self) -> dict[QWidget, str]:
        try:
            return self.__ignored
        except AttributeError:
            self.__ignored: dict[QWidget, str] = {}
            return self.__ignored
