"""The app's animated background: whether the theme's effect is on, and when it animates.

effects.py draws the effect; this module decides when. The app creates one `Backdrop`,
calls `start` once mounted, `apply` whenever the theme changes, and `focus_changed` when the
terminal window gains or loses focus.
"""

from __future__ import annotations

from textual.app import App
from textual.timer import Timer

from .effects import RAIN, RainField


class Backdrop:
    def __init__(self, app: App, widgets: str) -> None:
        """`widgets` selects, on the app's main screen, the backdrop widgets
        (effects.RainBackdrop) to redraw on every frame."""
        self._app = app
        self._widgets = widgets
        self.rain = RainField()  # read by the backdrop widgets
        self._timer: Timer | None = None

    @property
    def active(self) -> bool:
        """The theme has the effect on."""
        return self.rain.active

    @property
    def animating(self) -> bool:
        """Frames are being drawn: the effect is on and the terminal window has focus."""
        return self._timer is not None and self.rain.active and self._app.app_focus

    def start(self) -> None:
        self._timer = self._app.set_interval(1 / RainField.FPS, self._tick, pause=True)
        self._sync()

    def apply(self, theme: object) -> None:
        """Turn the effect on or off for `theme` (a themes.PlexTheme sets `effect`)."""
        active = getattr(theme, "effect", None) == RAIN
        if active == self.rain.active:
            return
        self.rain.active = active
        if active:
            self.rain.resize(*self._app.size)
        self._sync()
        self._redraw()

    def focus_changed(self) -> None:
        self._sync()

    def _sync(self) -> None:
        if self._timer is not None:
            (self._timer.resume if self.animating else self._timer.pause)()

    def _tick(self) -> None:
        self.rain.resize(*self._app.size)
        self.rain.tick()
        self._redraw()

    def _redraw(self) -> None:
        main = self._app.screen_stack[0]  # the chat screen, even while a dialog is open
        for widget in main.query(self._widgets):
            widget.refresh()
