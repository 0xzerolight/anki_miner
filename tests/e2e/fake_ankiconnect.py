"""In-memory fake AnkiConnect server for the E2E harness.

Replaces the old preview-mode "no-Anki path": a real loopback HTTP server that
speaks enough of the AnkiConnect v6 protocol for the full mining pipeline
(preflight, known-words query, duplicate probe, media upload, note creation)
and the harness gateway (deck ensure/read-back/cleanup) to run without a live
Anki. Injected via ``E2EConfig.ankiconnect_url`` — it binds loopback, so
``AnkiGateway.verify_safe`` accepts it, and it works cross-process (the soak
children reach it over TCP like any other AnkiConnect endpoint).

Deliberately minimal semantics, matched to what the app actually issues:

- ``findNotes`` understands the query terms the codebase produces: the
  gateway's ``deck:"Name"``, ``AnkiService._build_vocab_query``'s ``deck:*``
  whole-collection form, and ``note:"Type"``. Negations (``-deck:"..."``) and
  ``is:`` terms are tolerated and ignored — the harness never excludes decks.
- Duplicate detection mirrors Anki's: same model + same value in the MODEL's
  first field (its ``inOrderFields`` order, not the payload's key order),
  overridable per note via ``options.allowDuplicate``. The rejection string
  contains AnkiConnect's exact duplicate literal so
  ``AnkiService._probe_duplicates`` classifies it correctly. An empty first
  field is refused even with duplicates allowed, as AnkiConnect does.
- ``ThreadingHTTPServer``: the cross-process soak has children mining while
  the parent snapshots deck counts, so requests must not serialize on one
  socket.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = ["FakeAnkiConnect"]

#: The exact literal AnkiConnect embeds in a duplicate rejection; must match
#: ``anki_service._DUPLICATE_ERROR_SUBSTRING`` for the probe to classify it.
_DUPLICATE_ERROR = "cannot create note because it is a duplicate"
#: AnkiConnect's refusal for a note whose first field is empty.
_EMPTY_ERROR = "cannot create note because it is empty"
#: One ``findNotes`` term: optional ``-``, a key, then a quoted or bare value.
_QUERY_TERM = re.compile(r'(-?)(\w+):(?:"([^"]*)"|(\S+))')


class FakeAnkiConnect:
    """A loopback AnkiConnect v6 fake with an in-memory collection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decks: set[str] = {"Default"}
        #: model name -> ordered field names
        self._models: dict[str, list[str]] = {}
        #: note id -> {"deckName", "modelName", "fields": {name: value}, "tags"}
        self._notes: dict[int, dict[str, Any]] = {}
        self._media: dict[str, str] = {}
        self._next_note_id = 1000
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ----- lifecycle ------------------------------------------------------

    @property
    def url(self) -> str:
        """Endpoint URL; only valid after :meth:`start`."""
        assert self._server is not None, "FakeAnkiConnect not started"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeAnkiConnect:
        """Bind an ephemeral loopback port and serve on a daemon thread."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    payload = {}
                body = fake._envelope(payload)
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (http.server API name)
                pass  # keep pytest output clean

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-ankiconnect", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut the server down and join its thread."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def __enter__(self) -> FakeAnkiConnect:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ----- test seams -----------------------------------------------------

    def seed_model(self, name: str, fields: list[str]) -> None:
        """Register a note type without going through ``createModel``.

        Gateway-less driver tests hit the app's preflight (``modelNames`` +
        ``modelFieldNames``) with no one having created the harness model; the
        ``fake_anki`` fixture seeds it here.
        """
        with self._lock:
            self._models[name] = list(fields)

    def seed_deck(self, name: str) -> None:
        """Register a deck without going through ``createDeck``.

        Gateway-less driver tests hit the app's preflight, which now VERIFIES
        the deck exists (it no longer creates it); the ``fake_anki`` fixture
        seeds it here. ``_decks`` otherwise holds only "Default".
        """
        with self._lock:
            self._decks.add(name)

    def note_count(self, deck: str | None = None) -> int:
        """Notes in ``deck`` (or the whole collection) — a test-side read-back."""
        with self._lock:
            if deck is None:
                return len(self._notes)
            return sum(1 for n in self._notes.values() if n["deckName"] == deck)

    def notes(self, deck: str | None = None) -> list[dict[str, str]]:
        """Field values of the notes in ``deck`` (or all), by field name — a test-side read-back."""
        with self._lock:
            return [dict(n["fields"]) for n in self._notes.values() if deck is None or n["deckName"] == deck]

    # ----- protocol -------------------------------------------------------

    def _envelope(self, payload: dict) -> dict:
        action = payload.get("action")
        params = payload.get("params") or {}
        try:
            with self._lock:
                result = self._dispatch(action, params)
        except _FakeError as e:
            return {"result": None, "error": str(e)}
        return {"result": result, "error": None}

    def _dispatch(self, action: Any, params: dict) -> Any:  # noqa: C901 (flat protocol switch)
        if action == "version":
            return 6
        if action == "deckNames":
            return sorted(self._decks)
        if action == "createDeck":
            self._decks.add(params["deck"])
            return 1
        if action == "deleteDecks":
            for deck in params.get("decks") or []:
                self._decks.discard(deck)
                if params.get("cardsToo"):
                    self._notes = {nid: n for nid, n in self._notes.items() if n["deckName"] != deck}
            return None
        if action == "deleteNotes":
            for nid in params.get("notes") or []:
                self._notes.pop(nid, None)
            return None
        if action == "modelNames":
            return sorted(self._models)
        if action == "modelFieldNames":
            name = params.get("modelName")
            if name not in self._models:
                raise _FakeError(f"model was not found: {name}")
            return list(self._models[name])
        if action == "createModel":
            self._models[params["modelName"]] = list(params.get("inOrderFields") or [])
            return {"id": 1}
        if action == "findNotes":
            return self._find_notes(params.get("query") or "")
        if action == "notesInfo":
            return [self._note_info(nid) for nid in params.get("notes") or []]
        if action == "canAddNotesWithErrorDetail":
            return [
                {"canAdd": err is None, "error": err}
                for err in (self._addability_error(n) for n in params.get("notes") or [])
            ]
        if action == "canAddNotes":
            return [self._addability_error(n) is None for n in params.get("notes") or []]
        if action == "addNotes":
            return [self._add_note(n) for n in params.get("notes") or []]
        if action == "storeMediaFile":
            filename = params.get("filename") or ""
            data = params.get("data") or ""
            if not data and params.get("path"):
                # AnkiConnect opens the path itself; an unreadable one fails
                # this action only, and the app uploads the file inline.
                try:
                    with open(params["path"], "rb") as f:
                        data = base64.b64encode(f.read()).decode("ascii")
                except OSError as e:
                    raise _FakeError(str(e)) from e
            self._media[filename] = data
            return filename
        if action == "multi":
            results: list[dict] = []
            for sub in params.get("actions") or []:
                try:
                    results.append(
                        {"result": self._dispatch(sub.get("action"), sub.get("params") or {}), "error": None}
                    )
                except _FakeError as e:
                    results.append({"result": None, "error": str(e)})
            return results
        raise _FakeError(f"unsupported action: {action}")

    # ----- semantics ------------------------------------------------------

    def _find_notes(self, query: str) -> list[int]:
        """Answer the query terms the app issues (see module docstring)."""
        deck: str | None = None
        model: str | None = None
        for negated, key, quoted, bare in _QUERY_TERM.findall(query):
            value = quoted or bare
            if negated:
                continue
            if key == "deck" and value != "*":
                deck = value
            elif key == "note":
                model = value
        return sorted(
            nid
            for nid, n in self._notes.items()
            if (deck is None or n["deckName"] == deck) and (model is None or n["modelName"] == model)
        )

    def _note_info(self, nid: int) -> dict:
        note = self._notes.get(nid)
        if note is None:
            return {}  # AnkiConnect's deleted-note shape
        # Anki lists every field of the model, in the model's order; a model
        # never seeded or created keeps the payload's order.
        names = self._models.get(note["modelName"]) or list(note["fields"])
        return {
            "noteId": nid,
            "modelName": note["modelName"],
            "tags": list(note.get("tags") or []),
            "fields": {name: {"value": note["fields"].get(name, ""), "order": i} for i, name in enumerate(names)},
        }

    def _first_field_value(self, note: dict) -> str:
        """The value of the note's MODEL's first field, as Anki keys duplicates on it.

        A model never seeded or created falls back to the payload's first key.
        """
        fields = note.get("fields") or {}
        order = self._models.get(note.get("modelName") or "")
        if order:
            return str(fields.get(order[0], ""))
        if not fields:
            return ""
        return str(fields[next(iter(fields))])

    def _addability_error(self, note: dict) -> str | None:
        """None if addable; the empty or duplicate error string if it would be rejected."""
        first = self._first_field_value(note)
        if not first.strip():
            return _EMPTY_ERROR
        if note.get("options", {}).get("allowDuplicate"):
            return None
        model = note.get("modelName")
        for existing in self._notes.values():
            if existing["modelName"] == model and self._first_field_value(existing) == first:
                return _DUPLICATE_ERROR
        return None

    def _add_note(self, note: dict) -> int | None:
        if self._addability_error(note) is not None:
            return None  # AnkiConnect's null slot for a rejected duplicate
        nid = self._next_note_id
        self._next_note_id += 1
        self._notes[nid] = {
            "deckName": note.get("deckName") or "Default",
            "modelName": note.get("modelName") or "",
            "fields": {str(k): str(v) for k, v in (note.get("fields") or {}).items()},
            "tags": list(note.get("tags") or []),
        }
        return nid


class _FakeError(Exception):
    """Internal: becomes the ``error`` field of the response envelope."""
