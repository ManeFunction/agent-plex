"""Modal dialogs."""

from __future__ import annotations

from functools import partial
from typing import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import CommandInput, CommandPalette, DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..auth import AUTH_FILE
from ..config import EFFORT_INFO, EFFORTS, REASONING, REASONING_INFO
from ..session import ago
from .commands import COMMANDS


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_none", "Close", show=False)]

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ApiKeyScreen(Dialog):
    def __init__(self, reason: str = "") -> None:
        super().__init__()
        self.reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Content.from_markup("[b]Connect to Perplexity[/]"), classes="title")
            if self.reason:
                yield Static(Content.from_markup("[$warning]$r[/]", r=self.reason))
            yield Static(Content.from_markup(
                "[dim]Paste an API key from[/] [@click=app.open_url('https://www.perplexity.ai/account/api')]"
                "perplexity.ai/account/api[/]"))
            yield Input(placeholder="pplx-…", password=True, id="key")
            yield Static(Content.from_markup("[dim]Saved to $p (mode 600) · enter to save · esc to cancel[/]",
                                             p=str(AUTH_FILE).replace(str(AUTH_FILE.home()), "~")),
                         classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)


def effort_option(effort: str, seen: dict | None) -> Content:
    """Two lines: the level and what it does, then the model last seen serving it."""
    body = Content.from_markup("[b]$e[/]  [dim]$d[/]\n         ", e=f"{effort:<7}", d=EFFORT_INFO[effort])
    if seen:
        body += Content.from_markup("[$secondary]$m[/] [dim]· seen $a[/]", m=seen["model"], a=ago(seen["at"]))
    else:
        body += Content.from_markup("[dim]model shown after first use[/]")
    return body


class EffortScreen(Dialog):
    def __init__(self, current: str, seen: dict[str, dict] | None = None) -> None:
        super().__init__()
        self.current = current
        self.seen = seen or {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Content.from_markup("[b]Research effort[/]"), classes="title")
            opts = OptionList(*[Option(effort_option(e, self.seen.get(e)), id=e) for e in EFFORTS])
            yield opts
            yield Static(Content.from_markup(
                "[dim]Perplexity picks each level's model and may change it.\n"
                "enter to choose · esc to cancel[/]"), classes="hint")

    def on_mount(self) -> None:
        opts = self.query_one(OptionList)
        opts.highlighted = EFFORTS.index(self.current) if self.current in EFFORTS else 0
        opts.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


DEFAULT_REASONING = "default"  # option id for "let the model decide"


class ReasoningScreen(Dialog):
    """Reasoning effort for a manually selected model."""

    def __init__(self, model: str, current: str | None) -> None:
        super().__init__()
        self.model = model
        self.current = current or DEFAULT_REASONING

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Content.from_markup("[b]Reasoning effort[/] [dim]for[/] $m", m=self.model),
                         classes="title")
            levels = [(DEFAULT_REASONING, "the model's own default")]
            levels += [(r, REASONING_INFO[r]) for r in REASONING]
            yield OptionList(*[Option(Content.from_markup("[b]$r[/]  [dim]$d[/]", r=f"{r:<8}", d=d), id=r)
                               for r, d in levels])
            yield Static(Content.from_markup(
                "[dim]Not every model supports every level.\n"
                "/auto-effort goes back to presets · enter to choose · esc to cancel[/]"), classes="hint")

    def on_mount(self) -> None:
        opts = self.query_one(OptionList)
        opts.highlighted = opts.get_option_index(self.current)
        opts.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ConfirmScreen(Dialog):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n", "dismiss_none", "No"),
                Binding("escape", "dismiss_none", "No", show=False)]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Content(self.question), classes="title")
            yield Static(Content.from_markup("[b]y[/] [dim]yes ·[/] [b]n[/] [dim]no[/]"), classes="hint")

    def action_yes(self) -> None:
        self.dismiss(True)


KEYS = [
    ("enter", "send message"),
    ("shift+enter · alt+enter · ctrl+j", "new line"),
    ("↑ / ↓ (empty prompt)", "recall previous messages"),
    ("esc", "cancel the answer being written"),
    ("ctrl+n", "new conversation"),
    ("ctrl+s", "toggle history sidebar"),
    ("ctrl+e", "change auto effort preset"),
    ("ctrl+p", "command palette"),
    ("ctrl+q", "quit"),
]


class HelpScreen(Dialog):
    def compose(self) -> ComposeResult:
        body = Content.from_markup("[b]Keys[/]\n")
        for k, d in KEYS:
            body += Content.from_markup("  [$secondary]$k[/]  [dim]$d[/]\n", k=f"{k:<34}", d=d)
        body += Content.from_markup("\n[b]Commands[/]\n")
        for c in COMMANDS:
            body += Content.from_markup("  [$secondary]$n[/]  [dim]$d[/]\n", n=f"/{c.name:<33}", d=c.help)
        with Vertical(classes="dialog wide"):
            yield Static(body)
            yield Static(Content.from_markup("[dim]esc to close[/]"), classes="hint")


class ThemeProvider(Provider):
    """Lists themes; every hit carries the theme name as `text` for live preview."""

    def _themes(self) -> list[str]:
        return list(self.app.available_themes)  # plex's own order, see themes.py

    def _pick(self, name: str) -> None:
        self.app.theme = name

    async def discover(self) -> Hits:
        for name in self._themes():
            yield DiscoveryHit(name, partial(self._pick, name), text=name)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name in self._themes():
            if (score := matcher.match(name)) > 0:
                yield Hit(score, matcher.highlight(name), partial(self._pick, name), text=name)


class PickerPalette(CommandPalette):
    """A searchable list that opens on the hit whose `text` equals `initial`."""

    initial: str | None = None

    def _refresh_command_list(self, command_list, commands, clear_current) -> None:
        super()._refresh_command_list(command_list, commands, clear_current)
        # Open on the current choice rather than jumping to the first one in the list.
        if not self.query_one(CommandInput).value:
            for index, command in enumerate(command_list.options):
                if command.hit.text == self.initial:
                    command_list.highlighted = index
                    break


PRESET = "preset"  # the model-list entry that goes back to effort presets


class ModelProvider(Provider):
    """Lists the preset entry, then `models`; every hit carries its id as `text`.

    `then`, if set, runs after a model (not the preset entry) is picked.
    """

    models: list[str] = []
    then: Callable[[], None] | None = None

    def _entries(self) -> list[tuple[str, str, str]]:
        """(text, label, help) per entry."""
        preset = (PRESET, f"preset · {self.app.settings.effort}",
                  "Let Perplexity pick the model for the effort level")
        return [preset] + [(m, m, "") for m in self.models]

    def _pick(self, text: str) -> None:
        self.app.set_model(None if text == PRESET else text)
        if text != PRESET and self.then:
            self.then()

    async def discover(self) -> Hits:
        for text, label, help in self._entries():
            yield DiscoveryHit(label, partial(self._pick, text), text=text, help=help or None)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for text, label, help in self._entries():
            if (score := matcher.match(label)) > 0:
                yield Hit(score, matcher.highlight(label), partial(self._pick, text),
                          text=text, help=help or None)


class ModelPalette(PickerPalette):
    """Model search; enter switches to direct-model mode, the preset entry switches back."""

    def __init__(self, models: list[str], current: str | None,
                 then: Callable[[], None] | None = None) -> None:
        provider = type("ModelProvider", (ModelProvider,),
                        {"models": models, "then": staticmethod(then) if then else None})
        super().__init__(providers=[provider], placeholder="Search for models…")
        self.initial = current or PRESET


class ThemePalette(PickerPalette):
    """Theme search that applies each theme as it's highlighted; esc restores the original."""

    def __init__(self) -> None:
        super().__init__(providers=[ThemeProvider], placeholder="Search for themes…")
        self.original_theme: str | None = None

    def on_mount(self) -> None:
        self.original_theme = self.initial = self.app.theme

    @on(OptionList.OptionHighlighted)
    def _preview(self, event: OptionList.OptionHighlighted) -> None:
        name = getattr(getattr(event.option, "hit", None), "text", None)
        if name in self.app.available_themes:
            self.app.theme = name

    def _action_escape(self) -> None:
        if self.original_theme:
            self.app.theme = self.original_theme
        super()._action_escape()
