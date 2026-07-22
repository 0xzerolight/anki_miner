"""Guided first-run Setup Wizard package (Task 3).

Exports the wizard, its typed outcome, and its modal runner.
"""

from .setup_wizard import SetupWizard, SetupWizardOutcome, run_setup_wizard

__all__ = ["SetupWizard", "SetupWizardOutcome", "run_setup_wizard"]
