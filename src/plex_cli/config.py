"""Settings, persisted UI state and API-key resolution."""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .files import atomic_write_json

EFFORTS = ("fast", "low", "medium", "high", "xhigh", "ultra")

EFFORT_INFO = {
    "fast": "single quick search · cheapest (~$0.002)",
    "low": "a few research steps",
    "medium": "more thorough research",
    "high": "deep research, slower",
    "xhigh": "long multi-step research · expensive",
    "ultra": "maximum effort · most expensive",
}

# Reasoning effort for a manually selected model (`reasoning.effort` in the API).
REASONING = ("minimal", "low", "medium", "high", "xhigh", "max")

REASONING_INFO = {
    "minimal": "barely thinks · fastest",
    "low": "light reasoning",
    "medium": "balanced",
    "high": "thinks longer",
    "xhigh": "extended reasoning · slower",
    "max": "maximum reasoning · slowest",
}

DEFAULT_THEME = "terminal"

DEFAULT_INSTRUCTIONS = (
    "You are a helpful research assistant answering in a terminal chat. "
    "Be concise and use Markdown. When you use web sources, cite them inline "
    "with square brackets containing the source index, e.g. [1] or [2]; "
    "never group them like [1, 2] or [1-2]."
)


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_FILE = _xdg("XDG_CONFIG_HOME", ".config") / "plex" / "config.toml"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "plex"
SESSIONS_DIR = DATA_DIR / "sessions"
STATE_FILE = DATA_DIR / "state.json"


# Settings fields kept in state.json between launches. The mode fields are saved together,
# since a reasoning level belongs to the model it was set for.
MODE_KEYS = ("effort", "model", "reasoning")
STATE_KEYS = (*MODE_KEYS, "search", "theme")


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    """Per-request settings. `model` set means direct-model mode, else `effort` preset."""

    effort: str = "fast"
    model: str | None = None
    reasoning: str | None = None  # model mode only; None leaves it to the model's default
    search: bool = True
    instructions: str = DEFAULT_INSTRUCTIONS
    max_steps: int = 5
    theme: str = DEFAULT_THEME
    extra: dict = field(default_factory=dict)
    # Last model seen serving each effort level: {level: {"model", "version", "at"}}.
    seen: dict[str, dict] = field(default_factory=dict)

    # --- mode transitions: the rules for preset mode vs direct-model mode live here ---

    @property
    def is_preset(self) -> bool:
        """Perplexity picks the model for `effort`; otherwise `model` is used directly."""
        return not self.model

    def use_preset(self, effort: str) -> None:
        """Switch to an effort preset; drops a selected model."""
        if effort not in EFFORTS:
            raise ValueError(f"unknown effort {effort!r}")
        self.effort, self.model = effort, None

    def use_model(self, model: str | None) -> None:
        """Select a model directly (None goes back to presets). A new model starts from its
        default reasoning level, since not every model supports every level."""
        model = model or None
        if model != self.model:
            self.reasoning = None
        self.model = model

    def set_reasoning(self, level: str | None) -> None:
        """Reasoning effort for the selected model; None leaves it to the model."""
        if level is not None:
            if level not in REASONING:
                raise ValueError(f"unknown reasoning level {level!r}")
            if self.is_preset:
                raise ValueError("reasoning effort needs a selected model")
        self.reasoning = level

    def note_seen(self, effort: str | None, model: str | None, now: float | None = None) -> bool:
        """Record which model served an effort level. `effort` is as the API reports it,
        e.g. "fast@2026-11-11.0". Returns whether anything was recorded."""
        level, _, version = (effort or "").partition("@")
        if level not in EFFORTS or not model:
            return False
        self.seen[level] = {"model": model, "version": version or None, "at": now or time.time()}
        return True

    @property
    def label(self) -> str:
        if self.is_preset:
            mode = self.effort
        else:
            mode = f"{self.model}:{self.reasoning}" if self.reasoning else self.model
        return mode if self.search else f"{mode} · no search"


def load_file_config(path: Path = CONFIG_FILE) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Cannot read {path}: {e}") from e


def _apply(s: Settings, cfg: dict, source: str) -> None:
    if (e := cfg.get("effort")) is not None:
        if e not in EFFORTS:
            raise ConfigError(f"Invalid effort {e!r} in {source}; choose from {', '.join(EFFORTS)}")
        s.effort = e
    if "model" in cfg:
        s.model = cfg["model"] or None
    if "reasoning" in cfg:
        r = cfg["reasoning"] or None
        if r is not None and r not in REASONING:
            raise ConfigError(f"Invalid reasoning {r!r} in {source}; choose from {', '.join(REASONING)}")
        s.reasoning = r
    if "search" in cfg:
        s.search = bool(cfg["search"])
    if cfg.get("instructions"):
        s.instructions = cfg["instructions"]
    if "max_steps" in cfg:
        try:
            s.max_steps = int(cfg["max_steps"])
        except (TypeError, ValueError):
            raise ConfigError(f"Invalid max_steps {cfg['max_steps']!r} in {source}; use a whole number") from None
    if cfg.get("theme"):
        s.theme = str(cfg["theme"])


def settings_from(file_cfg: dict, state: dict | None = None) -> Settings:
    """config.toml defaults, then last-used UI state on top."""
    s = Settings()
    _apply(s, file_cfg, "config")
    if state:
        try:
            _apply(s, {k: state[k] for k in STATE_KEYS if k in state}, "state")
        except ConfigError:
            pass  # ignore a corrupt state file
        s.seen = _valid_seen(state.get("seen"))
    return s


def _valid_seen(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if k in EFFORTS and isinstance(v, dict) and isinstance(v.get("model"), str)
            and isinstance(v.get("at"), (int, float))}


def load_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or STATE_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict, path: Path) -> None:
    try:
        atomic_write_json(path, state)
    except OSError:
        pass  # remembering the last-used settings is best effort


def save_state(s: Settings, keys: Iterable[str] = STATE_KEYS, path: Path | None = None) -> None:
    """Write the `keys` fields of `s` into the state file, keeping everything else in it.

    Callers name only what the user just changed, so one-launch overrides from command-line
    flags (`plex -m ...`, `--no-search`) are not saved along with it.
    """
    path = path or STATE_FILE
    _write_state({**load_state(path), **{k: getattr(s, k) for k in keys}}, path)


def save_seen(seen: dict[str, dict], path: Path | None = None) -> None:
    """Update only the seen-models part of the state (one-shot mode keeps the rest as is)."""
    path = path or STATE_FILE
    _write_state({**load_state(path), "seen": seen}, path)


def api_key_source(file_cfg: dict) -> tuple[str, str] | None:
    """(key, source) for the key plex will use, or None. Sources, in order of precedence:
    "env" (PERPLEXITY_API_KEY), "auth" (auth.json, `plex auth login`), "config" (config.toml)."""
    from .auth import get_key

    for source, key in (("env", lambda: os.environ.get("PERPLEXITY_API_KEY")),
                        ("auth", get_key),
                        ("config", lambda: file_cfg.get("api_key"))):
        if k := key():
            return k, source
    return None


def resolve_api_key(file_cfg: dict) -> str | None:
    found = api_key_source(file_cfg)
    return found[0] if found else None
