"""Shared helpers for the Settings import-flow test modules.

Deliberately plain helpers, NOT ``conftest.py`` fixtures (same style as
``_queue_worker_harness.py``): each module builds a different SettingsTab
config and patches a different ``ImportWorker`` factory, so every module keeps
a thin fixture over these building blocks.

Everything here is synchronous. The flows' real ``_run_latest_scan`` runs work
on a QThread; ``run_scan_sync`` replaces it on the flow instance so a test
drives the scan callbacks inline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QMessageBox

_SIGNALS = ("progress", "import_finished", "failed", "cancelled", "finished")


def run_scan_sync(
    work: Callable[..., object],
    on_done: Callable[[object], None],
    on_error: Callable[[str], None],
    *,
    pass_cancel_check: bool = False,
) -> None:
    """Synchronous stand-in for ``ModalImportFlowMixin._run_latest_scan``."""
    try:
        on_done(work(lambda: False) if pass_cancel_check else work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


def capture_warnings(monkeypatch: Any) -> list[tuple[str, str]]:
    """Capture reported screen issues as ``(summary, whole text)`` (D24).

    Import failures are no longer modals: they land in the owning panel's
    banner, so the seam moved from ``QMessageBox.warning`` to the reporter.
    """
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "anki_miner.gui.controllers.import_flow_common.report_screen_issue",
        lambda origin, issue: captured.append((issue.summary, f"{issue.summary}\n{issue.details}".strip())) or True,
    )
    return captured


def capture_infos(monkeypatch: Any) -> list[tuple[str, str]]:
    """Capture ``QMessageBox.information`` calls as ``(title, body)``."""
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
    )
    return captured


def make_stub_worker(*, running: bool = False, args: tuple = (), kwargs: dict | None = None) -> MagicMock:
    """One stand-in ``ImportWorker``: MagicMock signals, no thread.

    ``running`` is what ``isRunning()`` reports, which decides whether a flow
    treats the worker as a live predecessor (``still_running``). A test drives
    the outcome by calling the slot the flow connected, e.g.
    ``instance.import_finished.connect.call_args[0][0](...)``.
    """
    instance = MagicMock(name="ImportWorker")
    for signal in _SIGNALS:
        setattr(instance, signal, MagicMock())
    instance.cancel = MagicMock()
    instance.start = MagicMock()
    instance.set_trace_id = MagicMock()
    instance.is_cancelled = False
    instance.isRunning = MagicMock(return_value=running)
    instance._args = args
    instance._kwargs = {} if kwargs is None else kwargs
    return instance


def patch_stub_worker_factories(
    monkeypatch: Any,
    target: str,
    repair_target: str,
    *,
    running: bool = False,
) -> MagicMock:
    """Patch an ``ImportWorker`` factory pair with mocks that build stub workers.

    Returns the primary factory mock. ``.instances`` lists every stub either
    factory built, in order; ``.repair_factory`` is the repair mock.
    """
    factory = MagicMock(name=target.rsplit(".", 1)[-1])
    repair_factory = MagicMock(name=repair_target.rsplit(".", 1)[-1])
    instances: list[MagicMock] = []

    def build(*args: Any, **kwargs: Any) -> MagicMock:
        instance = make_stub_worker(running=running, args=args, kwargs=kwargs)
        instances.append(instance)
        return instance

    factory.side_effect = build
    repair_factory.side_effect = build
    factory.instances = instances
    factory.repair_factory = repair_factory
    monkeypatch.setattr(target, factory)
    monkeypatch.setattr(repair_target, repair_factory, raising=False)
    return factory


def fire_done(instance: MagicMock, resource_id: str, meta: dict) -> None:
    """Deliver ``import_finished`` then the native ``finished`` barrier."""
    instance.import_finished.connect.call_args[0][0](resource_id, meta)
    fire_thread_finished(instance)


def fire_failed(instance: MagicMock, err: str) -> None:
    """Deliver ``failed`` then the native ``finished`` barrier."""
    instance.failed.connect.call_args[0][0](err)
    fire_thread_finished(instance)


def fire_cancelled(instance: MagicMock) -> None:
    """Deliver ``cancelled`` then the native ``finished`` barrier."""
    instance.cancelled.connect.call_args[0][0]()
    fire_thread_finished(instance)


def fire_thread_finished(instance: MagicMock) -> None:
    """Deliver the worker's native ``finished`` signal."""
    instance.finished.connect.call_args[0][0]()
