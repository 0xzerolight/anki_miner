"""Guided first-run Setup Wizard package (Task 3).

Exports the wizard and its runner. ``run_setup_wizard(parent, config)`` mirrors
``run_resource_download``: it runs modally and returns the (possibly partial)
working config on both Accepted and Rejected.
"""

from .setup_wizard import SetupWizard, run_setup_wizard

__all__ = ["SetupWizard", "run_setup_wizard"]
