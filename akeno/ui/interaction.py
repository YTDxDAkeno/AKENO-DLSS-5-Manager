"""Mouse-wheel rules for every AKENO window, and the control polish that goes with them.

By default Qt lets a spin box, combo box, slider or tab bar take the wheel, and keyboard focus, as soon as the
pointer is over it. A page scrolled with the wheel then changes values on the way. AKENO's rules:

* The wheel never gives a control keyboard focus.
* A value control reacts to the wheel only when it has keyboard focus (the user clicked or tabbed into it) and the
  scroll starts on it. Otherwise the wheel scrolls whatever is under the control, normally the page.
* While the wheel scrolls the page, it keeps scrolling the page, even when a focused control comes under the pointer.
  Only a pause of ``LATCH`` seconds ends the page scroll.

Nested scroll areas (lists, tables, logs) keep scrolling themselves. Qt hands the wheel on to the page when they
reach their end, so they never trap the page.

The same filter makes sure no spin box is ever narrower than its widest value with its unit, wherever a page puts
it (``spin_min_width``).
"""

from __future__ import annotations

import time

import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar, QTabBar,
                               QWidget)

LATCH = 0.6                       # seconds of wheel pause that end a page scroll
GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider, QTabBar)
_WHEEL = QEvent.Type.Wheel
_POLISH = QEvent.Type.Polish
_WHEEL_FOCUS = Qt.FocusPolicy.WheelFocus
_STRONG_FOCUS = Qt.FocusPolicy.StrongFocus


def _guarded(w) -> bool:
    return isinstance(w, GUARDED) and not isinstance(w, QScrollBar)


def _ptr(w) -> int:
    return shiboken6.getCppPointer(w)[0]


def spin_text_width(w) -> int:
    """Width of the widest text a spin box shows (lowest and highest value with prefix and unit, or its format)."""
    fm = w.fontMetrics()
    texts = [w.lineEdit().text() if w.lineEdit() else ""]
    if hasattr(w, "textFromValue"):
        for v in (w.minimum(), w.maximum()):
            texts.append(w.prefix() + w.textFromValue(v) + w.suffix())
        if w.specialValueText():
            texts.append(w.specialValueText())
    elif hasattr(w, "displayFormat"):
        texts.append(w.displayFormat())
    return max(fm.horizontalAdvance(x) for x in texts)


def spin_min_width(w) -> int:
    """Text, left padding, arrow buttons, frame and a little air (theme.py's QAbstractSpinBox rules)."""
    return spin_text_width(w) + 9 + 4 + 24 + 2 + 12


def control_under(widget: QWidget):
    """The value control a wheel event over ``widget`` belongs to (a spin box for its text field), if any."""
    w = widget
    while w is not None:
        if _guarded(w):
            return w
        if w.isWindow() or isinstance(w, QAbstractScrollArea):
            return None
        w = w.parentWidget()
    return None


class WheelGuard(QObject):
    """Application event filter enforcing the rules above. ``clock`` is replaceable for tests."""

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock = clock
        self._page_until = 0.0        # the page scroll goes on until then
        self._latched = 0             # the control the wheel is adjusting right now
        self._blocked = 0             # the control the current wheel event must pass by

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == _WHEEL:
            return self._wheel(obj, event)
        if kind == _POLISH and obj.isWidgetType():
            if obj.focusPolicy() == _WHEEL_FOCUS:
                obj.setFocusPolicy(_STRONG_FOCUS)
            if isinstance(obj, QAbstractSpinBox) and obj.lineEdit() is not None:
                need = spin_min_width(obj)
                if obj.minimumWidth() < need:
                    obj.setMinimumWidth(need)
        return False

    def _wheel(self, obj, event) -> bool:
        if not obj.isWidgetType():
            return False
        if event.spontaneous():                       # first delivery of a real wheel turn: decide once
            self._decide(obj)
        if self._blocked and _guarded(obj) and _ptr(obj) == self._blocked:
            event.ignore()                            # not for this control: Qt hands it on to its parents
            return True
        return False

    def _decide(self, receiver: QWidget) -> None:
        now = self.clock()
        ctrl = control_under(receiver)
        if ctrl is None:
            self._page_until, self._latched, self._blocked = now + LATCH, 0, 0
            return
        key = _ptr(ctrl)
        if ctrl.isEnabled() and ctrl.hasFocus() and (self._latched == key or now >= self._page_until):
            self._page_until, self._latched, self._blocked = 0.0, key, 0
            return
        self._page_until, self._latched, self._blocked = now + LATCH, 0, key


def install(app) -> WheelGuard:
    """Install the guard on the application once; later calls return the installed guard."""
    guard = getattr(app, "_akeno_wheel_guard", None)
    if guard is not None and shiboken6.isValid(guard):
        return guard
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    app._akeno_wheel_guard = guard
    return guard
