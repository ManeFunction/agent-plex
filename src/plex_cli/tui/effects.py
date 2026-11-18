"""Animated background effects that a theme can turn on (`effect = "rain"`).

The rain is one screen-sized field. Widgets that form the app's background
(`RainBackdrop`) draw their slice of it into their empty cells, so it falls
around messages, the input and the sidebar without running behind any text.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from rich.segment import Segment
from rich.style import Style
from textual.containers import Vertical, VerticalScroll
from textual.geometry import Region
from textual.strip import Strip

EFFECTS = {"rain"}

# Half-width katakana (one cell wide, like the film's code) plus digits and a few symbols.
GLYPHS = "ｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ0123456789:.=*+-<>¦"

# Head first, then the trail fading out. Kept dim so it stays in the background.
SHADES = ["#3DAA55", "#13802C", "#0E6B23", "#0B581D", "#084516", "#063510", "#04260B"]
_SHADE_STYLES = [Style(color=c) for c in SHADES]

GAP = 2  # cells kept clear around text drawn by a backdrop widget (e.g. the welcome logo)


@dataclass
class Drop:
    y: float      # head row
    speed: float  # rows per tick
    length: int   # trail length


class RainField:
    FPS = 8
    DENSITY = 0.45  # share of columns with a drop falling at any moment

    def __init__(self) -> None:
        self.active = False
        self.width = self.height = 0
        self._drops: list[Drop | None] = []
        self._glyphs: list[list[str]] = []
        self._shade: list[list[int]] = []  # per cell: index into SHADES, or -1 for empty
        self._rows: dict[tuple[int, Style], Strip] = {}  # full screen rows built this frame
        self._styles: dict[Style, list[Style]] = {}

    def resize(self, width: int, height: int) -> None:
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self._glyphs = [[random.choice(GLYPHS) for _ in range(width)] for _ in range(height)]
        self._drops = [self._new_drop(anywhere=True) if random.random() < self.DENSITY else None
                       for _ in range(width)]
        self._update_shade()

    def _new_drop(self, anywhere: bool = False) -> Drop:
        h = self.height
        start = random.uniform(0, h) if anywhere else -random.uniform(0, h / 2)
        return Drop(y=start, speed=random.uniform(0.5, 1.4), length=random.randint(6, max(8, h // 2)))

    def tick(self) -> None:
        w, h = self.width, self.height
        if not w or not h:
            return
        for x, drop in enumerate(self._drops):
            if drop is None:
                if random.random() < self.DENSITY / 20:
                    self._drops[x] = self._new_drop()
            else:
                drop.y += drop.speed
                if drop.y - drop.length > h:
                    self._drops[x] = None
        for _ in range(max(1, w * h // 60)):  # a few glyphs change in place, as in the film
            self._glyphs[random.randrange(h)][random.randrange(w)] = random.choice(GLYPHS)
        self._update_shade()

    def _update_shade(self) -> None:
        w, h = self.width, self.height
        shade = [[-1] * w for _ in range(h)]
        last = len(SHADES) - 1
        for x, drop in enumerate(self._drops):
            if drop is None:
                continue
            head = int(drop.y)
            for behind in range(drop.length):
                y = head - behind
                if 0 <= y < h:
                    shade[y][x] = 0 if behind == 0 else min(last, 1 + behind * last // drop.length)
        self._shade = shade
        self._rows.clear()

    def _row(self, y: int, base: Style) -> Strip:
        """One full screen row of rain, built once per frame and shared by every backdrop."""
        key = (y, base)
        if (strip := self._rows.get(key)) is not None:
            return strip
        styles = self._styles.get(base)
        if styles is None:
            styles = self._styles[base] = [base + s for s in _SHADE_STYLES]
        shades, glyphs = self._shade[y], self._glyphs[y]
        segments: list[Segment] = []
        start = 0
        for x in range(1, self.width + 1):
            if x < self.width and shades[x] == shades[start]:
                continue
            idx = shades[start]
            if idx < 0:
                segments.append(Segment(" " * (x - start), base))
            else:
                segments.append(Segment("".join(glyphs[start:x]), styles[idx]))
            start = x
        strip = self._rows[key] = Strip(segments, self.width)
        return strip

    def strip(self, x0: int, y: int, width: int, base: Style) -> Strip:
        """Screen row `y`, columns x0..x0+width, drawn over the `base` (background) style."""
        if width <= 0:
            return Strip([], 0)
        if not 0 <= y < self.height or x0 < 0 or x0 + width > self.width:
            return Strip.blank(width, base)
        return self._row(y, base).crop(x0, x0 + width)

    def overlay(self, strip: Strip, x0: int, y: int) -> Strip:
        """Fill the blank cells of `strip` (screen row y, from column x0) with rain,
        keeping any text plus a small margin around it clear."""
        text = strip.text
        width = strip.cell_length
        if len(text) != width:  # wide characters: leave the line alone
            return strip
        segments = list(strip)
        base = Style(bgcolor=segments[0].style.bgcolor) if segments and segments[0].style else Style()
        start = len(text) - len(text.lstrip(" "))
        if start == width:
            return self.strip(x0, y, width, base)
        end = len(text.rstrip(" "))
        left, right = max(0, start - GAP), min(width, end + GAP)
        return Strip.join([
            self.strip(x0, y, left, base),
            strip.crop(left, right),
            self.strip(x0 + right, y, width - right, base),
        ])


class RainBackdrop:
    """Mixin for background widgets: draws the app's rain into their blank cells."""

    TEXTLESS = False  # True for containers: their own cells are all blank, so skip the text scan

    def render_lines(self, crop: Region) -> list[Strip]:
        rain: RainField | None = getattr(self.app, "rain", None)  # type: ignore[attr-defined]
        if rain is None or not rain.active:
            return super().render_lines(crop)  # type: ignore[misc]
        region = self.region  # type: ignore[attr-defined]
        x0, y0 = region.x + crop.x, region.y + crop.y
        if self.TEXTLESS:
            base = Style(bgcolor=self.rich_style.bgcolor)  # type: ignore[attr-defined]
            return [rain.strip(x0, y0 + i, crop.width, base) for i in range(crop.height)]
        lines = super().render_lines(crop)  # type: ignore[misc]
        return [rain.overlay(line, x0, y0 + i) for i, line in enumerate(lines)]


class BackdropVertical(RainBackdrop, Vertical):
    TEXTLESS = True


class BackdropScroll(RainBackdrop, VerticalScroll):
    TEXTLESS = True
