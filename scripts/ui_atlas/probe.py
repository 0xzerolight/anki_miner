"""Widget dump + the mechanical checkers the atlas runs on every screen.

Everything runs IN-PROCESS against live widgets, never over serialised JSON:
several checkers need layout, ``QFormLayout`` and event-filter facts a dump
cannot carry, and a hidden ``QTabWidget`` page reports a stale 640x480 that would
make the geometry checkers fire on fiction.

Checker design notes (each was verified, several the hard way):

* #1/#2 replace ``height < minimumSizeHint``, which is FALSE on the exact issue
  #102 geometry (a crushed ``QTableWidget`` reports ``minimumSizeHint == (70, 70)``)
  and which cannot fire for a ``QTableWidget`` at all, since it IS a
  ``QAbstractScrollArea``.
* #3 cannot use ``qSmartMinSize`` — not exported to PyQt6. ``layout().minimumSize()``
  is the reachable equivalent, guarded to widgets that own a layout.
* #4 must skip wordWrap / RichText / ``ElidingLabel`` or it is false by construction.
* #5 must use an ABSOLUTE rect: ``QWidget.geometry()`` is parent-relative.
* #10 and #11 are new for the post-overhaul atlas: they are the falsifiable
  oracles for D6 (the run button must never be below the window edge) and D10
  (Settings must not need a scrolling tab strip).
"""

from __future__ import annotations

import contextlib

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTabBar,
    QWidget,
)

try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget
except Exception:  # pragma: no cover - optional Qt module
    QOpenGLWidget = ()  # type: ignore[assignment]

GLYPH_CHARS = set("✕×↑↓★☆⟳↻⚙…▸▾▴◂+-@")

#: ``ModernButton`` writes its variant into the object name; ``primary`` is the
#: one task action on a screen (D41). This is how the atlas finds "the run button".
PRIMARY_OBJECT_NAME = "primary"

#: Object name of the pinned workflow action bar (D6). Imported as a literal
#: rather than from the widget module so ``probe`` stays importable before Qt
#: widgets are constructed; the value is asserted against the module in
#: ``tests/unit/test_ui_atlas_harness.py``.
ACTION_BAR_OBJECT_NAME = "workflow-action-bar"


def _has_scroll_ancestor(w: QWidget, root: QWidget) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if isinstance(p, QAbstractScrollArea):
            return True
        p = p.parentWidget()
    return False


def _abs_rect(w: QWidget, root: QWidget):
    try:
        tl = w.mapTo(root, QPoint(0, 0))
    except Exception:
        return None
    return (tl.x(), tl.y(), w.width(), w.height())


def _visible_rect(w: QWidget, root: QWidget) -> QRect | None:
    """``w``'s rect in ``root`` coordinates, clipped by every scrolling ancestor.

    An empty result means the widget is on a page that exists but is not on
    screen — the exact shape of "the run button is below the window edge".
    """
    r = _abs_rect(w, root)
    if r is None:
        return None
    rect = QRect(*r)
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            vp = p.viewport()
            if vp is not None:
                tl = vp.mapTo(root, QPoint(0, 0))
                rect = rect.intersected(QRect(tl.x(), tl.y(), vp.width(), vp.height()))
        if p is root:
            break
        p = p.parentWidget()
    return rect.intersected(QRect(0, 0, root.width(), root.height()))


def dump_tree(root: QWidget, screen: str) -> dict:
    """Dump only the VISIBLE subtree of ``root``, with absolute rects."""
    records = []
    skipped = 0
    for w in root.findChildren(QWidget):
        if not w.isVisible():
            skipped += 1
            continue
        rec = {
            "cls": type(w).__name__,
            "objectName": w.objectName(),
            "geometry": [w.x(), w.y(), w.width(), w.height()],
            "abs_rect": _abs_rect(w, root),
            "sizeHint": [w.sizeHint().width(), w.sizeHint().height()],
            "minimumSizeHint": [w.minimumSizeHint().width(), w.minimumSizeHint().height()],
            "minimumHeight": w.minimumHeight(),
            "minimumWidth": w.minimumWidth(),
            "maximumHeight": w.maximumHeight(),
            "isEnabled": w.isEnabled(),
            "toolTip": w.toolTip(),
            "accessibleName": w.accessibleName(),
        }
        lay = w.layout()
        if lay is not None:
            ms = lay.minimumSize()
            rec["layout_minimumSize"] = [ms.width(), ms.height()]
            rec["layout_cls"] = type(lay).__name__
        if isinstance(w, QLabel):
            rec["text"] = w.text()[:300]
            rec["wordWrap"] = w.wordWrap()
            rec["textFormat"] = str(w.textFormat())
            rec["textWidth"] = w.fontMetrics().horizontalAdvance(w.text())
        if isinstance(w, QAbstractButton):
            rec["text"] = w.text()[:200]
            rec["checkable"] = w.isCheckable()
        if isinstance(w, QProgressBar):
            # A still frame cannot distinguish a marquee from a finished bar.
            rec["progress"] = {
                "min": w.minimum(),
                "max": w.maximum(),
                "value": w.value(),
                "format": w.format(),
                "isTextVisible": w.isTextVisible(),
                "indeterminate": w.minimum() == 0 and w.maximum() == 0,
            }
        if isinstance(w, QAbstractItemView):
            vp = w.viewport()
            row_h = 0
            with contextlib.suppress(Exception):
                row_h = w.sizeHintForRow(0)
            rec["itemview"] = {
                "viewport_h": vp.height() if vp else 0,
                "row_h": row_h,
                "rows": w.model().rowCount() if w.model() else 0,
            }
        records.append(rec)

    return {
        "screen": screen,
        "achieved_window_size": [root.width(), root.height()],
        "contains_gl_widget": bool(QOpenGLWidget) and bool(root.findChildren(QOpenGLWidget)),
        "visible": len(records),
        "skipped_invisible": skipped,
        "widgets": records,
    }


def settle(app, root: QWidget) -> bool:
    """Two layout passes; report whether geometry stopped moving."""
    app.processEvents()
    if root.layout() is not None:
        root.layout().activate()
    app.processEvents()
    before = [(w.x(), w.y(), w.width(), w.height()) for w in root.findChildren(QWidget) if w.isVisible()]
    app.processEvents()
    after = [(w.x(), w.y(), w.width(), w.height()) for w in root.findChildren(QWidget) if w.isVisible()]
    return before == after


# --------------------------------------------------------------------------
# Checkers. Each returns a list of finding dicts.
# --------------------------------------------------------------------------


def check_01_itemview_crush(root: QWidget, screen: str) -> list[dict]:
    """Issue #102: a populated item view squeezed below ~5 visible rows."""
    out = []
    for w in root.findChildren(QAbstractItemView):
        if not w.isVisible():
            continue
        model = w.model()
        rows = model.rowCount() if model else 0
        if rows <= 0:
            continue
        try:
            row_h = w.sizeHintForRow(0) or 0
        except Exception:
            row_h = 0
        if row_h <= 0:
            continue
        header_h = 0
        hh = getattr(w, "horizontalHeader", None)
        if callable(hh) and hh() is not None and hh().isVisible():
            header_h = hh().height()
        vis = (w.viewport().height() - header_h) / row_h
        # Calibrated against the #102 precedent, not guessed: the pre-fix table
        # showed 3.37 rows and the post-fix one 11.4, and the fix's own
        # setMinimumHeight(240) is ~5 rows. Raw pixels are reported alongside
        # because row height is font-dependent.
        #
        # ``vis < rows`` is the second half of the test and it is not redundant:
        # a two-row chain list that shows four rows hides nothing, and reporting
        # it as a crush drowns the real ones.
        if vis < 5 and vis < rows:
            out.append(
                {
                    "checker": "01_itemview_crush",
                    "screen": screen,
                    "widget": f"{type(w).__name__}#{w.objectName()}",
                    "visible_rows": round(vis, 2),
                    "rows_in_model": rows,
                    "viewport_h": w.viewport().height(),
                    "row_h": row_h,
                    "detail": f"only {vis:.2f} rows visible of {rows}",
                }
            )
    return out


def check_02_no_scroll_crush(root: QWidget, screen: str) -> list[dict]:
    """Visible widget with NO scroll-area escape hatch rendered below its sizeHint."""
    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisible() or w.height() <= 0:
            continue
        if isinstance(w, QAbstractScrollArea) or _has_scroll_ancestor(w, root):
            continue
        # A container that OWNS a scroll area already has the escape hatch.
        if w.findChildren(QAbstractScrollArea):
            continue
        sh = w.sizeHint().height()
        if sh > 0 and w.height() < sh:
            deficit = sh - w.height()
            if deficit < 8:  # sub-pixel / rounding noise
                continue
            out.append(
                {
                    "checker": "02_no_scroll_crush",
                    "screen": screen,
                    "widget": f"{type(w).__name__}#{w.objectName()}",
                    "height": w.height(),
                    "sizeHint_h": sh,
                    "deficit": deficit,
                    "detail": f"rendered {w.height()}px vs sizeHint {sh}px, no scroll ancestor",
                }
            )
    return out


def check_03_explicit_min_below_layout(root: QWidget, screen: str) -> list[dict]:
    """An explicit minimum SMALLER than the layout demands."""
    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisible():
            continue  # hidden tab pages carry a stale 640x480 and un-activated layouts
        lay = w.layout()
        if lay is None:
            continue
        lmin = lay.minimumSize()
        for axis, explicit, demanded in (
            ("height", w.minimumHeight(), lmin.height()),
            ("width", w.minimumWidth(), lmin.width()),
        ):
            if explicit > 0 and demanded > 0 and explicit < demanded:
                out.append(
                    {
                        "checker": "03_explicit_min_below_layout",
                        "screen": screen,
                        "widget": f"{type(w).__name__}#{w.objectName()}",
                        "axis": axis,
                        "explicit_min": explicit,
                        "layout_min": demanded,
                        "detail": f"setMinimum{axis.capitalize()}({explicit}) is below layout minimum {demanded}",
                    }
                )
    return out


def check_04_text_clipped(root: QWidget, screen: str) -> list[dict]:
    """Text wider than its box. Skips wordWrap / RichText / ``ElidingLabel``."""
    out = []
    for w in root.findChildren(QLabel):
        if not w.isVisible() or not w.text():
            continue
        if type(w).__name__ == "ElidingLabel":
            continue
        if w.wordWrap():
            continue
        txt = w.text()
        if "<" in txt or w.textFormat() == Qt.TextFormat.RichText:
            continue
        avail = w.contentsRect().width()
        need = w.fontMetrics().horizontalAdvance(txt)
        if avail > 0 and need > avail + 2:
            out.append(
                {
                    "checker": "04_text_clipped",
                    "screen": screen,
                    "widget": f"QLabel#{w.objectName()}",
                    "text": txt[:120],
                    "need_px": need,
                    "avail_px": avail,
                    "detail": f"text needs {need}px in a {avail}px box",
                }
            )
    return out


def check_05_horizontal_overflow(root: QWidget, screen: str) -> list[dict]:
    """Absolute rect extending past the CAPTURED WINDOW BOUNDS (never "the screen")."""
    out = []
    w_max = root.width()
    for w in root.findChildren(QWidget):
        if not w.isVisible():
            continue
        r = _abs_rect(w, root)
        if r is None:
            continue
        x, y, ww, hh = r
        if ww <= 0 or hh <= 0:
            continue
        if _has_scroll_ancestor(w, root):
            continue  # scrolling is the escape hatch, by design
        if x + ww > w_max + 2:
            out.append(
                {
                    "checker": "05_horizontal_overflow",
                    "screen": screen,
                    "widget": f"{type(w).__name__}#{w.objectName()}",
                    "right_edge": x + ww,
                    "window_w": w_max,
                    "detail": f"right edge {x + ww}px past window width {w_max}px",
                }
            )
    return out


def check_06_zero_geometry(root: QWidget, screen: str) -> list[dict]:
    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisible():
            continue
        # Qt's own internal children (qt_scrollarea_viewport, corner buttons) and
        # empty header views legitimately collapse to zero: plumbing, not UX.
        if w.objectName().startswith("qt_") or isinstance(w, QHeaderView):
            continue
        if w.width() <= 0 or w.height() <= 0:
            out.append(
                {
                    "checker": "06_zero_geometry",
                    "screen": screen,
                    "widget": f"{type(w).__name__}#{w.objectName()}",
                    "geometry": [w.x(), w.y(), w.width(), w.height()],
                    "detail": "visible widget with zero/negative size",
                }
            )
    return out


def check_07_glyph_button_unlabelled(root: QWidget, screen: str) -> list[dict]:
    out = []
    for b in root.findChildren(QAbstractButton):
        if not b.isVisible():
            continue
        if b.objectName().startswith("qt_"):
            continue  # Qt plumbing (e.g. qt_menubar_ext_button)
        txt = b.text().strip()
        is_glyph = (not txt) or (len(txt) <= 2 and any(c in GLYPH_CHARS for c in txt))
        if not is_glyph:
            continue
        if b.toolTip().strip() or b.accessibleName().strip():
            continue
        if b.icon().isNull() and not txt:
            continue  # spacer-ish; not a real affordance
        out.append(
            {
                "checker": "07_glyph_button_unlabelled",
                "screen": screen,
                "widget": f"{type(b).__name__}#{b.objectName()}",
                "text": txt,
                "detail": "glyph-only button with no toolTip and no accessibleName",
            }
        )
    return out


def check_08_formlayout_no_buddy(root: QWidget, screen: str) -> list[dict]:
    """A ``QFormLayout`` label with no ``setBuddy`` has no keyboard mnemonic target."""
    out = []
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None:
                continue
            lw = item.widget()
            if isinstance(lw, QLabel) and lw.buddy() is None and lw.text().strip():
                out.append(
                    {
                        "checker": "08_formlayout_no_buddy",
                        "screen": screen,
                        "widget": f"QLabel#{lw.objectName()}",
                        "row": row,
                        "label": lw.text()[:80],
                        "detail": "QFormLayout label has no setBuddy (no keyboard mnemonic target)",
                    }
                )
    return out


def check_09_scroll_wheel_hazard(root: QWidget, screen: str) -> list[dict]:
    """Issue #99: spin/combo inside a ``QScrollArea`` without the no-scroll filter.

    Event filters are not enumerable, so this keys on the two OBSERVABLE side
    effects: the ``_NoScrollEventFilter`` child object, and ``StrongFocus`` on
    every spin/combo.
    """
    from anki_miner.gui.utils.qt_helpers import _NoScrollEventFilter

    out = []
    for sa in root.findChildren(QScrollArea):
        if not sa.isVisible():
            continue
        inner = sa.widget()
        if inner is None:
            continue
        inputs = inner.findChildren(QComboBox) + inner.findChildren(QAbstractSpinBox)
        if not inputs:
            continue
        has_filter = bool(inner.findChildren(_NoScrollEventFilter))
        weak = [i for i in inputs if i.focusPolicy() != Qt.FocusPolicy.StrongFocus]
        if not has_filter or weak:
            out.append(
                {
                    "checker": "09_scroll_wheel_hazard",
                    "screen": screen,
                    "widget": f"QScrollArea#{sa.objectName()}",
                    "inputs": len(inputs),
                    "has_no_scroll_filter": has_filter,
                    "weak_focus_inputs": len(weak),
                    "detail": (
                        f"{len(inputs)} spin/combo inside a scroll area; "
                        f"filter={'yes' if has_filter else 'NO'}, weak-focus={len(weak)} "
                        "-> hover-wheel can mutate values instead of scrolling"
                    ),
                }
            )
    return out


def _in_pinned_bar(w: QWidget, root: QWidget) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if p.objectName() == ACTION_BAR_OBJECT_NAME:
            return True
        p = p.parentWidget()
    return False


def check_10_primary_action_hidden(root: QWidget, screen: str) -> list[dict]:
    """D6: the screen's primary action must not be off the window edge.

    The 2026-07-25 audit's headline defect was seven screens whose run button sat
    at or below the window edge at 1024x768 / German / 150%. The pinned action
    bar (``widgets/base/workflow_action_bar.py``) is the accepted fix, so this is
    its oracle: a ``primary`` button whose visible rect is smaller than its own
    rect is clipped, either by the window or by a scrolling ancestor.

    ``severity`` separates the two, because they are not the same defect:

    ``unreachable``
        The action cannot be brought on screen at this size — it is outside the
        window, or it is inside the *pinned bar*, which by contract never
        scrolls. This is the D6 regression signal.
    ``below_fold``
        The action is inside ordinary page scroll, so it is reachable by
        scrolling. Reported, not a D6 failure: Settings pages and Deck Builder
        (D3 still open) legitimately scroll their content.
    """
    out = []
    for b in root.findChildren(QAbstractButton):
        if not b.isVisible() or b.objectName() != PRIMARY_OBJECT_NAME:
            continue
        own = _abs_rect(b, root)
        vis = _visible_rect(b, root)
        if own is None or vis is None:
            continue
        if b.width() <= 0 or b.height() <= 0:
            continue
        if vis.width() >= b.width() and vis.height() >= b.height():
            continue
        pinned = _in_pinned_bar(b, root)
        scrollable = _has_scroll_ancestor(b, root)
        severity = "below_fold" if (scrollable and not pinned) else "unreachable"
        out.append(
            {
                "checker": "10_primary_action_hidden",
                "screen": screen,
                "widget": f"{type(b).__name__}#{b.objectName()}",
                "severity": severity,
                "text": b.text()[:80],
                "abs_rect": list(own),
                "visible_rect": [vis.x(), vis.y(), vis.width(), vis.height()],
                "window": [root.width(), root.height()],
                "in_scroll_area": scrollable,
                "in_pinned_bar": pinned,
                "detail": (
                    f"[{severity}] primary action {own[2]}x{own[3]} at y={own[1]} renders only "
                    f"{vis.width()}x{vis.height()} inside a {root.width()}x{root.height()} window"
                ),
            }
        )
    return out


def check_12_pinned_bar_clipped(root: QWidget, screen: str) -> list[dict]:
    """D6: where a workflow shell exists, its bar must be wholly on screen.

    Separate from #10 so the contract still has an oracle on a screen whose bar
    carries no ``primary`` button (a tool screen mid-run shows Cancel instead).
    """
    out = []
    for bar in root.findChildren(QWidget):
        if not bar.isVisible() or bar.objectName() != ACTION_BAR_OBJECT_NAME:
            continue
        own = _abs_rect(bar, root)
        vis = _visible_rect(bar, root)
        if own is None or vis is None:
            continue
        if vis.width() >= bar.width() and vis.height() >= bar.height():
            continue
        out.append(
            {
                "checker": "12_pinned_bar_clipped",
                "screen": screen,
                "widget": f"{type(bar).__name__}#{bar.objectName()}",
                "abs_rect": list(own),
                "visible_rect": [vis.x(), vis.y(), vis.width(), vis.height()],
                "window": [root.width(), root.height()],
                "detail": (
                    f"pinned action bar {bar.width()}x{bar.height()} renders only "
                    f"{vis.width()}x{vis.height()} in a {root.width()}x{root.height()} window"
                ),
            }
        )
    return out


def check_11_tabbar_overflow(root: QWidget, screen: str) -> list[dict]:
    """D10: a tab strip whose tabs do not fit gets Qt's scroll arrows.

    Settings used to be ten equally-weighted tabs that overflowed into arrows at
    the hostile cell; the grouped navigator replaced them with a list. Any
    remaining ``QTabBar`` that overflows is reported with the measured widths.
    """
    out = []
    for bar in root.findChildren(QTabBar):
        if not bar.isVisible() or bar.count() == 0:
            continue
        needed = sum(bar.tabRect(i).width() for i in range(bar.count()))
        if needed <= bar.width() + 2:
            continue
        out.append(
            {
                "checker": "11_tabbar_overflow",
                "screen": screen,
                "widget": f"{type(bar).__name__}#{bar.objectName()}",
                "tabs": bar.count(),
                "needed_px": needed,
                "available_px": bar.width(),
                "uses_scroll_buttons": bar.usesScrollButtons(),
                "labels": [bar.tabText(i) for i in range(bar.count())],
                "detail": (
                    f"{bar.count()} tabs need {needed}px in a {bar.width()}px strip "
                    f"-> {'scroll arrows' if bar.usesScrollButtons() else 'elision'}"
                ),
            }
        )
    return out


ALL_CHECKERS = [
    check_01_itemview_crush,
    check_02_no_scroll_crush,
    check_03_explicit_min_below_layout,
    check_04_text_clipped,
    check_05_horizontal_overflow,
    check_06_zero_geometry,
    check_07_glyph_button_unlabelled,
    check_08_formlayout_no_buddy,
    check_09_scroll_wheel_hazard,
    check_10_primary_action_hidden,
    check_11_tabbar_overflow,
    check_12_pinned_bar_clipped,
]


def run_checkers(root: QWidget, screen: str) -> list[dict]:
    findings = []
    for fn in ALL_CHECKERS:
        try:
            findings.extend(fn(root, screen))
        except Exception as exc:  # a broken checker must not kill the sweep
            findings.append({"checker": fn.__name__, "screen": screen, "error": f"{type(exc).__name__}: {exc}"})
    return findings
