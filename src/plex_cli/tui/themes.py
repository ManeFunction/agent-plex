"""Load color themes from TOML files: the bundled ones plus the user's own.

Each `<name>.toml` file is one theme, named after the file. Files in the user's
theme folder (~/.config/plex/themes) are loaded after the bundled ones, so a
user file with the same name replaces a bundled theme.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from textual.theme import Theme

from ..config import CONFIG_FILE
from .effects import EFFECTS

BUNDLED_THEMES_DIR = Path(__file__).parent.parent / "themes"
USER_THEMES_DIR = CONFIG_FILE.parent / "themes"
USER_THEMES_PLACEHOLDER = "PUT_YOUR_THEMES_HERE"



@dataclass
class PlexTheme(Theme):
    effect: str | None = None
    """An animated background from effects.py, e.g. "rain"."""


_THEME_KEYS = {f.name for f in fields(PlexTheme)} - {"name", "ansi"}

# `terminal = true` themes start from these: the terminal's own background and
# text show through, lines are dim gray, and only the accent carries color.
_TERMINAL_BASE = {
    "ansi": True,
    "secondary": "ansi_bright_black",
    "accent": "ansi_blue",
    "warning": "ansi_yellow",
    "error": "ansi_red",
    "success": "ansi_green",
    "foreground": "ansi_default",
    "background": "ansi_default",
    "surface": "ansi_default",
    "panel": "ansi_default",
    "boost": "ansi_default",
    "dark": True,
}

_TERMINAL_VARIABLES = {
    "ansi-background": "ansi_default",
    "ansi-foreground": "ansi_default",
    "border-blurred": "ansi_bright_black",
    "input-cursor-background": "ansi_default",
    "input-cursor-foreground": "ansi_default",
    "input-cursor-text-style": "reverse",
    "input-selection-background": "ansi_bright_black",
    "input-selection-foreground": "ansi_default",
    "screen-selection-background": "ansi_bright_black",
    "screen-selection-foreground": "ansi_default",
    "scrollbar": "ansi_bright_black",
    "scrollbar-hover": "ansi_bright_black",
    "scrollbar-active": "ansi_bright_black",
    "scrollbar-background": "ansi_default",
    "scrollbar-background-hover": "ansi_default",
    "scrollbar-background-active": "ansi_default",
    "scrollbar-corner-color": "ansi_default",
    "footer-background": "ansi_default",
    # Highlighted rows: accent-colored bold text instead of a filled bar.
    "block-cursor-foreground": "$accent",
    "block-cursor-background": "ansi_default",
    "block-cursor-text-style": "bold",
    "block-cursor-blurred-foreground": "$accent",
    "block-cursor-blurred-background": "ansi_default",
    "block-cursor-blurred-text-style": "none",
    "block-hover-background": "ansi_default",
}


class ThemeError(Exception):
    pass


def parse_theme(name: str, data: dict) -> PlexTheme:
    """Build a Theme from the contents of one theme file."""
    data = dict(data)
    terminal = data.pop("terminal", False)
    if not isinstance(terminal, bool):
        raise ThemeError("`terminal` must be true or false")
    unknown = set(data) - _THEME_KEYS
    if unknown:
        raise ThemeError(f"unknown key(s): {', '.join(sorted(unknown))}")
    variables = data.pop("variables", {})
    if not isinstance(variables, dict):
        raise ThemeError("[variables] must be a table")
    if data.get("effect") is not None and data["effect"] not in EFFECTS:
        raise ThemeError(f"unknown effect {data['effect']!r}; available: {', '.join(sorted(EFFECTS))}")

    values = {**_TERMINAL_BASE, **data} if terminal else data
    if terminal:
        variables = {**_TERMINAL_VARIABLES, **variables}
    values.setdefault("primary", values.get("accent"))
    if not values.get("primary"):
        raise ThemeError("set `primary` or `accent`")
    # "$accent" in the terminal defaults stands for the theme's accent color.
    accent = values.get("accent") or values["primary"]
    variables = {k: accent if v == "$accent" else str(v) for k, v in variables.items()}

    try:
        theme = PlexTheme(name=name, variables=variables, **values)
        theme.to_color_system().generate()  # rejects bad colors now, not mid-render
    except Exception as e:
        raise ThemeError(str(e)) from None
    return theme


def ensure_user_themes_dir(folder: Path) -> None:
    """Create the user's theme folder with a blank placeholder file that shows
    where themes go. The placeholder is only added when the folder is new, so
    deleting it sticks. A read-only home is not an error: plex just runs with
    the bundled themes."""
    if folder.is_dir():
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / USER_THEMES_PLACEHOLDER).touch()
    except OSError:
        pass


def load_themes(user_dir: Path | None = None) -> tuple[list[PlexTheme], list[str]]:
    """Return (themes, problems). Terminal themes first, then the rest, each A–Z;
    themes with an animated effect go last. With no `user_dir`, the user's theme
    folder is created first if it doesn't exist yet."""
    if user_dir is None:
        user_dir = USER_THEMES_DIR
        ensure_user_themes_dir(user_dir)
    found: dict[str, PlexTheme] = {}
    problems: list[str] = []
    for folder in (BUNDLED_THEMES_DIR, user_dir):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.toml")):
            try:
                with path.open("rb") as f:
                    found[path.stem] = parse_theme(path.stem, tomllib.load(f))
            except (OSError, tomllib.TOMLDecodeError, ThemeError) as e:
                problems.append(f"{path}: {e}")
    themes = sorted(found.values(), key=lambda t: (t.effect is not None, not t.ansi, t.name))
    return themes, problems
