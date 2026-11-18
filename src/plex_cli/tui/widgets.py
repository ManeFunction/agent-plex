"""Chat widgets: messages, prompt, command menu, welcome banner."""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlparse

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..client import Source
from ..config import Settings
from .commands import COMMANDS, lookup
from .effects import RainBackdrop

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

LOGO = """\
█▀█ █   █▀▀ ▀▄▀
█▀▀ █▄▄ ██▄ █ █"""

def domain(url: str) -> str:
    host = urlparse(url).netloc or url
    return host.removeprefix("www.")


class Welcome(RainBackdrop, Static):
    def __init__(self, settings: Settings) -> None:
        super().__init__(id="welcome")
        self.settings = settings  # read on each render, so it follows /auto-effort and /model

    def render(self) -> Content:
        hints = [
            ("enter", "send"), ("shift+enter", "new line"), ("/", "commands"),
            ("ctrl+s", "history"), ("ctrl+n", "new chat"), ("ctrl+p", "palette"),
        ]
        body = Content.from_markup(f"[$accent b]{LOGO}[/]\n\n")
        body += Content.from_markup("[dim]Ask anything — answers are researched on the web.[/]\n\n")
        # Join (not suffix) with spaces: a trailing gap would push each centered line left.
        body += Content("   ").join(
            Content.from_markup("[$secondary b]$k[/] [dim]$w[/]", k=key, w=what) for key, what in hints
        )
        kind = "effort" if self.settings.is_preset else "model"
        body += Content.from_markup("\n\n[dim]$k[/] [b]$l[/]", k=kind, l=self.settings.label)
        return body


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(Content(text), classes="user")


class AssistantMessage(Vertical):
    """Header, live status/spinner, markdown body, sources, meta line."""

    def __init__(self, label: str, text: str = "") -> None:
        super().__init__(classes="assistant")
        self.label = label
        self._initial = text
        self._status = "Thinking"
        self._frame = 0
        self._started = time.monotonic()
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(Content.from_markup("[$accent b]◆ plex[/] [dim]$l[/]", l=self.label), classes="who")
        yield Static(classes="status")
        yield Markdown(self._initial, classes="answer")
        yield Static(classes="sources")
        yield Static(classes="meta")

    @property
    def markdown(self) -> Markdown:
        return self.query_one(".answer", Markdown)

    # --- live state ----------------------------------------------------------

    def start(self) -> None:
        self._started = time.monotonic()
        self.query_one(".status").display = True
        self._timer = self.set_interval(0.08, self._tick)
        self._tick()

    def set_status(self, text: str) -> None:
        self._status = text
        self._tick()

    def answering(self) -> None:
        self._stop_spinner()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(SPINNER)
        elapsed = time.monotonic() - self._started
        self.query_one(".status", Static).update(Content.from_markup(
            "[$accent]$s[/] [dim]$t · $e[/]", s=SPINNER[self._frame], t=self._status, e=f"{elapsed:.1f}s"))

    def _stop_spinner(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None
        self.query_one(".status").display = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    # --- final states --------------------------------------------------------

    def finish(self, sources: list[Source], meta: str, link_ids: list[int]) -> None:
        self._stop_spinner()
        self.show_sources(sources, link_ids)
        self.query_one(".meta", Static).update(Content.from_markup("[dim]$m[/]", m=meta))

    def show_sources(self, sources: list[Source], link_ids: list[int]) -> None:
        box = self.query_one(".sources", Static)
        if not sources:
            box.display = False
            return
        lines = Content.from_markup("[dim]Sources[/]")
        for s, lid in zip(sources, link_ids):
            lines += Content("\n") + Content.from_markup(
                f"[$accent]$n[/]  [@click=app.open_link({lid})]$t[/]  [dim]$d[/]",
                n=f"{s.id:>2}", t=s.title or domain(s.url), d=domain(s.url))
        box.update(lines)
        box.display = True

    def fail(self, msg: str) -> None:
        self._stop_spinner()
        self.add_class("failed")
        self.query_one(".meta", Static).update(Content.from_markup("[$error]✗ $m[/]", m=msg))

    def interrupted(self) -> None:
        self._stop_spinner()
        self.query_one(".meta", Static).update(Content.from_markup("[$warning]◼ interrupted[/]"))


class CommandMenu(OptionList):
    """Slash-command suggestions shown above the prompt."""

    def filter(self, prefix: str) -> bool:
        prefix = prefix.lower()
        matches = [(c.name, c.help) for c in COMMANDS if c.name.startswith(prefix)]
        self.clear_options()
        for n, d in matches:
            self.add_option(Option(Content.from_markup("[b]/$n[/]  [dim]$d[/]", n=n, d=d), id=n))
        if matches:
            self.highlighted = 0
        return bool(matches)

    @property
    def selected_command(self) -> str | None:
        if self.highlighted is None or not self.option_count:
            return None
        return self.get_option_at_index(self.highlighted).id


class PromptInput(TextArea):
    """Enter submits; shift/alt+enter or ctrl+j insert a newline."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    NEWLINE_KEYS = {"shift+enter", "alt+enter", "ctrl+j", "newline", "ctrl+enter"}

    def __init__(self, **kwargs) -> None:
        super().__init__(soft_wrap=True, show_line_numbers=False, compact=True,
                         highlight_cursor_line=False, **kwargs)
        self.menu: CommandMenu | None = None
        self.past_messages: Callable[[], list[str]] = lambda: []  # oldest first, for ↑/↓ recall
        self._recall_index: int | None = None
        self._recalling = False  # the text is a recalled message, so ↑/↓ keep browsing

    def reset_recall(self) -> None:
        self._recall_index = None
        self._recalling = False

    def _recall(self, direction: int) -> None:
        past = self.past_messages()
        if not past:
            return
        if self._recall_index is None:
            if direction > 0:
                return
            self._recall_index = len(past)
        self._recall_index += direction
        if self._recall_index >= len(past):
            self.reset_recall()
            self.text = ""
            return
        self._recall_index = max(0, self._recall_index)
        self.text = past[self._recall_index]
        self.move_cursor(self.document.end)
        self._recalling = True

    def _menu_open(self) -> bool:
        return bool(self.menu and self.menu.display and self.menu.option_count)

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if self._menu_open():
            if key in ("up", "down"):
                event.prevent_default(); event.stop()
                (self.menu.action_cursor_up if key == "up" else self.menu.action_cursor_down)()
                return
            if key in ("tab", "enter"):
                event.prevent_default(); event.stop()
                cmd = self.menu.selected_command
                if cmd:
                    if key == "tab" or lookup(cmd).fill_on_enter:
                        self.text = f"/{cmd} "
                        self.move_cursor(self.document.end)
                    else:
                        self.post_message(self.Submitted(f"/{cmd}"))
                return
            if key == "escape":
                event.prevent_default(); event.stop()
                self.menu.display = False
                return
        if key == "enter":
            event.prevent_default(); event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if key in self.NEWLINE_KEYS:
            event.prevent_default(); event.stop()
            self.insert("\n")
            return
        # ↑ on an empty prompt starts browsing past messages; while browsing, ↑/↓ move through them.
        if (key == "up" and not self.text) or (key in ("up", "down") and self._recalling):
            event.prevent_default(); event.stop()
            self._recall(-1 if key == "up" else 1)
            return
        self._recalling = False
        await super()._on_key(event)
