"""PyInstaller hook for webrtcvad, overriding the pyinstaller-hooks-contrib one.

ffsubsync (the primary retime engine) pulls in webrtcvad for its audio VAD
path, which put the module into the PyInstaller import graph for the first
time. That fires the contributed stdhook, which does
``copy_metadata("webrtcvad")`` and dies with PackageNotFoundError: the
distribution we install is ``webrtcvad-wheels``, a prebuilt-wheel fork that
provides the ``webrtcvad`` module under a different project name.

Spec ``hookspath`` entries carry HOOK_PRIORITY_USER_HOOKS (1000) against the
contributed hooks' -1000, so this file wins and the metadata is collected
under the name actually installed.
"""

from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
