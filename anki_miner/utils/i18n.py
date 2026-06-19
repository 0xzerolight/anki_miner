"""Helpers for interpolating Qt-style placeholders into translated strings.

PyQt6's QObject.tr() / QCoreApplication.translate() return a plain ``str``
(no QString.arg()), so %1/%2 substitution into a translated template is done
here. Plurals use Qt's %n form via the 3/4-arg tr()/translate() call instead.
"""

import re


def tr_format(template: str, *args: object) -> str:
    """Substitute Qt-style ``%1``, ``%2`` … placeholders left-to-right.

    ``tr_format("Step %1: %2", "parse", 3) == "Step parse: 3"``. Each
    ``%N`` (1-based) is replaced by ``str(args[N-1])``; placeholders with
    no corresponding arg are left untouched. A single regex pass avoids the
    ``%1`` / ``%10`` prefix-collision a naive ``str.replace`` would cause.
    """

    def _sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return str(args[idx - 1]) if 1 <= idx <= len(args) else m.group(0)

    return re.sub(r"%(\d+)", _sub, template)
