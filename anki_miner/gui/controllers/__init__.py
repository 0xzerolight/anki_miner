"""Controllers owning worker lifecycles + dialogs on behalf of GUI tabs.

One-way dependency: tab (view) → controller → workers/services. Controllers
never import or call back into the tab; collaboration points the tab owns
(config assembly, signal emission) are injected as callables.
"""
