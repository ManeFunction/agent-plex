"""Chat widgets: messages, prompt, command menu, welcome banner."""

from __future__ import annotations

import time
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
from ..render import fmt_cost
from .effects import RainBackdrop

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

LOGO = """\
█▀█ █   █▀▀ ▀▄▀
█▀▀ █▄▄ ██▄ █ █"""

# name, description, takes_argument
COMMANDS: list[tuple[str, str, bool]] = [
    ("exit", "Exit plex", False),
    ("history", "Browse chat history", False),
    ("auto-effort", "Pick a research effort preset (drops a selected model)", False),
    ("model", "Pick a model from a list, or /model <id> · /model off", False),
    ("effort", "Reasoning effort for the selected model", False),
    ("clear", "Start a new conversation", False),
    ("search", "Toggle web search", False),
    ("sources", "List sources of the last answer", False),
    ("copy", "Copy the last answer to the clipboard", False),
    ("rename", "Rename this conversation: /rename <title>", True),
    ("delete", "Delete this conversation", False),
    ("new", "Start a new conversation", False),
    ("theme", "Change the color theme", False),
    ("login", "Set your Perplexity API key", False),
    ("help", "Keys and commands", False),
    ("quit", "Exit plex", False),
]
COMMAND_ARGS = {name: takes for name, _, takes in COMMANDS}


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
        for key, what in hints:
            body += Content.from_markup("[$secondary b]$k[/] [dim]$w[/]   ", k=key, w=what)
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
        matches = [(n, d) for n, d, _ in COMMANDS if n.startswith(prefix)]
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

    class HistoryRequest(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    NEWLINE_KEYS = {"shift+enter", "alt+enter", "ctrl+j", "newline", "ctrl+enter"}

    def __init__(self, **kwargs) -> None:
        super().__init__(soft_wrap=True, show_line_numbers=False, compact=True,
                         highlight_cursor_line=False, **kwargs)
        self.menu: CommandMenu | None = None

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
                    if key == "tab" or COMMAND_ARGS.get(cmd):
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
        if key in ("up", "down") and (not self.text or getattr(self, "_recalling", False)):
            if key == "up" or self.text:
                event.prevent_default(); event.stop()
                self.post_message(self.HistoryRequest(-1 if key == "up" else 1))
                return
        self._recalling = False
        await super()._on_key(event)
