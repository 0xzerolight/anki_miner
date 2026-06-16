"""Safety-gated AnkiConnect gateway for the E2E harness.

This is the ONLY component that talks to the user's REAL local Anki for test
setup, cleanup, and read-back. Because it issues *mutating* AnkiConnect actions
(``createDeck``, ``deleteNotes``, ``deleteDecks``) against a live collection, it
is wrapped in three safety guards:

1. **Loopback-only.** The endpoint host must be ``127.0.0.1`` / ``localhost`` /
   ``::1``; a remote host is refused at construction so the harness can never
   mutate someone else's Anki.
2. **Deck-name invariant.** Every mutating call hard-asserts that the deck it
   operates on equals ``config.deck_name``, and only ever interpolates that name
   into a query — never an arbitrary deck.
3. **No foreign-deck adoption.** ``ensure_test_deck`` refuses to proceed if the
   distinctively named test deck already exists *with notes in it* (a real deck
   that happens to share the name), unless ``allow_existing=True`` is passed.

All AnkiConnect traffic goes through :func:`anki_miner.services._ankiconnect.post_action`,
which returns the action ``result`` and raises ``AnkiConnectionError`` on
connection failure, transport error, or an AnkiConnect-side ``error`` payload.
"""

from typing import Any
from urllib.parse import urlparse

import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services._ankiconnect import post_action
from tests.e2e.config import E2EConfig

# Hosts we consider safe to mutate (the local machine only).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class AnkiUnreachableError(RuntimeError):
    """Raised when AnkiConnect cannot be reached (Anki not running / wrong URL)."""


class ForeignDeckError(RuntimeError):
    """Raised when the test deck already exists with notes the harness didn't create.

    Guards against adopting (and later deleting) a real user deck that happens to
    share ``config.deck_name``.
    """


class AnkiGateway:
    """Thin, SAFE wrapper over ``post_action`` scoped to one E2E test deck."""

    def __init__(self, config: E2EConfig) -> None:
        """Build the gateway and immediately enforce the loopback guard.

        Args:
            config: Harness config supplying the deck name and AnkiConnect URL.

        Raises:
            ValueError: If ``config.ankiconnect_url`` is not a loopback host.
        """
        self.config = config
        # Tracks whether this gateway instance has already vetted/created the
        # deck this session, so the foreign-deck check only gates the FIRST
        # ensure (a deck the harness itself populated must not later trip it).
        self._deck_ensured = False
        self.verify_safe()

    # ----- safety guards -------------------------------------------------

    def verify_safe(self) -> None:
        """Refuse a non-loopback AnkiConnect host.

        Raises:
            ValueError: If the configured URL targets a remote host.
        """
        host = (urlparse(self.config.ankiconnect_url).hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"Refusing to operate against non-loopback AnkiConnect host "
                f"{host!r} (url={self.config.ankiconnect_url!r}). "
                f"The E2E harness mutates a live Anki collection and only ever "
                f"talks to {', '.join(sorted(_LOOPBACK_HOSTS))}."
            )

    def _assert_test_deck(self, name: str) -> None:
        """Hard-assert ``name`` is exactly the configured test deck.

        Centralised so every mutating path routes through one check that a
        future bug cannot bypass.

        Raises:
            AssertionError: If ``name`` differs from ``config.deck_name``.
        """
        assert name == self.config.deck_name, (
            f"Refusing to mutate deck {name!r}; the harness may only touch its "
            f"own test deck {self.config.deck_name!r}."
        )

    # ----- low-level call ------------------------------------------------

    def _call(self, action: str, params: dict | None = None, timeout: int = 30) -> Any:
        """Send one AnkiConnect action, mapping connection failure to a harness error.

        AnkiConnect-side ``error`` payloads still propagate as
        ``AnkiConnectionError`` (they are real server errors, not "unreachable").
        """
        try:
            return post_action(self.config.ankiconnect_url, action, params, timeout)
        except AnkiConnectionError as e:
            # post_action raises "Cannot connect..." with ``from`` set to a
            # requests.exceptions.ConnectionError ONLY on a refused/dropped
            # socket. NB this is NOT a subclass of the builtin ConnectionError
            # (it descends from RequestException/IOError), so we match the
            # requests type explicitly. Other transport failures (Timeout / JSON
            # decode) and AnkiConnect-side ``error`` payloads must keep their
            # AnkiConnectionError meaning. (Don't sniff the message: the literal
            # "AnkiConnect" contains the substring "connect".)
            if isinstance(e.__cause__, requests.exceptions.ConnectionError):
                raise AnkiUnreachableError(f"AnkiConnect unreachable at {self.config.ankiconnect_url}: {e}") from e
            raise

    # ----- read helpers --------------------------------------------------

    def ping(self) -> str | None:
        """Return the AnkiConnect API version, or raise if unreachable.

        Returns:
            The version (AnkiConnect returns an int; coerced to ``str``), or
            ``None`` if the server responds without a result.

        Raises:
            AnkiUnreachableError: If AnkiConnect cannot be reached.
        """
        result = self._call("version", timeout=10)
        return None if result is None else str(result)

    def notes_in_deck(self) -> list[int]:
        """Return note IDs currently in the test deck via ``findNotes``.

        The query interpolates ONLY ``config.deck_name`` (never an arbitrary
        deck), keeping the mutating-by-query helpers safe.
        """
        result = self._call(
            "findNotes",
            params={"query": f'deck:"{self.config.deck_name}"'},
            timeout=30,
        )
        return list(result or [])

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        """Return ``notesInfo`` for the given note IDs (empty in -> empty out)."""
        if not note_ids:
            return []
        result = self._call("notesInfo", params={"notes": note_ids}, timeout=30)
        return list(result or [])

    def deck_card_count(self) -> int:
        """Count notes in the test deck.

        Implemented as ``len(notes_in_deck())`` rather than ``findCards``: the
        harness creates one card per note and only ever needs a presence/size
        signal, so reusing the already-safe ``findNotes`` query avoids a second
        query shape (and a second place a deck name could be interpolated).
        """
        return len(self.notes_in_deck())

    # ----- mutating helpers (guarded) ------------------------------------

    def ensure_test_deck(self, allow_existing: bool = False) -> None:
        """Idempotently create the test deck after running the safety guards.

        Foreign-deck rule: on this gateway's FIRST ensure, if the deck already
        exists with at least one note, refuse with :class:`ForeignDeckError`
        (it is presumably a real deck sharing the name) unless ``allow_existing``
        is True. ``createDeck`` itself is idempotent, so re-ensuring a deck the
        harness already populated is fine and skips the foreign check.

        Args:
            allow_existing: Adopt a pre-existing, already-populated deck instead
                of refusing it.

        Raises:
            AssertionError: deck-name invariant violation (defensive).
            ForeignDeckError: pre-existing populated deck and not ``allow_existing``.
            AnkiUnreachableError: AnkiConnect unreachable.
        """
        self._assert_test_deck(self.config.deck_name)
        if not self._deck_ensured and not allow_existing:
            existing = self.deck_card_count()
            if existing > 0:
                raise ForeignDeckError(
                    f"Test deck {self.config.deck_name!r} already exists with "
                    f"{existing} note(s) the harness did not create. Refusing to "
                    f"adopt it (it may be a real deck). Pass allow_existing=True "
                    f"to override, or rename/clear the deck."
                )
        self._call("createDeck", params={"deck": self.config.deck_name}, timeout=15)
        self._deck_ensured = True

    def delete_test_deck_notes(self) -> int:
        """Delete exactly the notes currently in the test deck via ``deleteNotes``.

        Returns:
            Number of note IDs submitted for deletion (0 if the deck is empty).

        Raises:
            AssertionError: deck-name invariant violation (defensive).
            AnkiUnreachableError: AnkiConnect unreachable.
        """
        self._assert_test_deck(self.config.deck_name)
        note_ids = self.notes_in_deck()
        if not note_ids:
            return 0
        self._call("deleteNotes", params={"notes": note_ids}, timeout=30)
        return len(note_ids)

    def delete_test_deck(self) -> None:
        """Delete the whole test deck (cards too) via ``deleteDecks``.

        ``AnkiService`` has no deck-deletion helper, so this calls the
        ``deleteDecks`` action directly. Only ``config.deck_name`` is ever placed
        in the ``decks`` list.

        Raises:
            AssertionError: deck-name invariant violation (defensive).
            AnkiUnreachableError: AnkiConnect unreachable.
        """
        self._assert_test_deck(self.config.deck_name)
        self._call(
            "deleteDecks",
            params={"decks": [self.config.deck_name], "cardsToo": True},
            timeout=30,
        )
        self._deck_ensured = False
