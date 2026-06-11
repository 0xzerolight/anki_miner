"""Controllers owning worker lifecycles + dialogs on behalf of GUI views.

One-way dependency: view (window/tab) → controller → workers/services.
Controllers never import or call back into the view; collaboration points the
view owns (config assembly, signal emission) are injected as callables.
"""

from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

__all__ = ["BackgroundTaskController"]
