"""The plex full-screen chat app."""

from __future__ import annotations

import asyncio
import inspect
from functools import partial
from pathlib import Path
from typing import Callable, Iterable

from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Markdown, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from .. import auth
from ..citations import footer_sources, for_display, normalize
from .. import turn
from ..client import AsyncClient, Source, api_error_message
from ..config import DEFAULT_THEME, EFFORTS, MODE_KEYS, SESSIONS_DIR, Settings, save_state
from ..render import fmt_cost, plural
from ..session import Session, Store, ago
from .screens import (ApiKeyScreen, ConfirmScreen, EffortScreen, HelpScreen,
                      ModelPalette, ReasoningScreen, ThemePalette, parse_reasoning)
from .commands import COMMANDS, lookup
from .backdrop import Backdrop
from .effects import BackdropScroll, BackdropVertical
from .themes import load_themes
from .widgets import AssistantMessage, CommandMenu, PromptInput, UserMessage, Welcome


class StreamingView:
    """Shows a streaming answer in an AssistantMessage (a `turn.TurnView`)."""

    def __init__(self, msg: AssistantMessage) -> None:
        self.msg = msg
        # Created on the first chunk: stopping a never-run MarkdownStream raises CancelledError.
        self.stream = None

    async def delta(self, text: str) -> None:
        if self.stream is None:
            self.msg.answering()
            self.stream = Markdown.get_stream(self.msg.markdown)
            await asyncio.sleep(0)  # let the stream task start
        await self.stream.write(text)

    def searching(self, queries: list[str]) -> None:
        self.msg.set_status(turn.searching_status(queries))

    def sources_found(self, sources: list[Source]) -> None:
        if self.stream is None:
            self.msg.set_status(f"Reading {len(sources)} sources")

    async def stop(self) -> None:
        if self.stream is not None:
            await self.stream.stop()


class PlexApp(App):
    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "plex"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New chat", priority=True),
        Binding("ctrl+s", "toggle_sidebar", "History", priority=True),
        Binding("ctrl+e", "pick_effort", "Preset", priority=True),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, settings: Settings, api_key: str | None,
                 session: Session | None = None, store: Store | None = None,
                 client_factory=AsyncClient) -> None:
        super().__init__()
        self.settings = settings
        self.store = store or Store(SESSIONS_DIR)
        self.session = session or Session()
        self._client_factory = client_factory
        self.client = client_factory(api_key) if api_key else None
        self._worker: Worker | None = None
        self.is_busy = False  # an answer is being written
        self._links: list[str] = []
        self._pending: str | None = None  # question waiting for an API key
        self._models: list[str] | None = None  # /v1/models, fetched on first /model
        self.backdrop = Backdrop(self, "#main, #chat, Welcome")  # the theme's animated background

    # --- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static(Content.from_markup("[b]History[/]  [dim]ctrl+s[/]"), id="sidebar-title")
                yield OptionList(id="sessions")
            with BackdropVertical(id="main"):
                # Neither takes focus on click, so typing always goes to the prompt.
                yield BackdropScroll(id="chat", can_focus=False)
                yield CommandMenu(id="menu")
                with Vertical(id="prompt-box"):
                    yield PromptInput(id="prompt", placeholder="Ask anything…  (/ for commands)")
                with Horizontal(id="statusbar"):
                    yield Static(id="status-left")
                    yield Static(id="status-right")

    async def on_mount(self) -> None:
        self.backdrop.start()
        self.setup_themes()
        self.backdrop.apply(self.current_theme)
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        prompt = self.query_one(PromptInput)
        prompt.menu = self.query_one(CommandMenu)
        prompt.past_messages = lambda: [t.user for t in self.session.turns]
        self.query_one(CommandMenu).display = False
        self.query_one("#sidebar").display = False
        self.query_one("#chat").anchor()
        self.watch(self.screen, "focused", self._keep_focus, init=False)
        await self.show_session(self.session)
        prompt.focus()
        if self.client is None:
            self.ask_for_key()

    def setup_themes(self) -> None:
        """Offer only the themes from theme files (see themes.py), in their order."""
        themes, problems = load_themes()
        if themes:  # without any, Textual's own themes stay
            for name in list(self.available_themes):
                self.unregister_theme(name)
            for theme in themes:
                self.register_theme(theme)
        names = list(self.available_themes)
        self.theme = next(n for n in (self.settings.theme, DEFAULT_THEME, *names) if n in names)
        if problems:
            self.notify("\n".join(problems), title="Some themes were skipped",
                        severity="warning", timeout=15)

    def _keep_focus(self, focused) -> None:
        """Clicking a blank or non-focusable spot clears focus: give it back to the prompt."""
        if focused is None and self.screen is self.screen_stack[0]:
            self.query_one(PromptInput).focus()

    def watch_app_focus(self, focused: bool) -> None:
        self.backdrop.focus_changed()

    def _on_theme_changed(self, theme) -> None:
        # Text styled with theme colors ($accent etc.) keeps the old colors until redrawn.
        for widget in self.screen_stack[0].query("*"):
            widget.refresh()
        self.backdrop.apply(theme)
        if isinstance(self.screen, ThemePalette):
            return  # previewing; saved once the picker closes
        self.save_theme()

    def save_theme(self) -> None:
        if self.settings.theme != self.theme:
            self.settings.theme = self.theme
            save_state(self.settings, ("theme",))

    def action_change_theme(self) -> None:
        self.push_screen(ThemePalette(), lambda _: self.save_theme())

    # --- status / sidebar ------------------------------------------------------

    def refresh_status(self) -> None:
        s = self.settings
        # Distinct glyphs: ◆ effort preset, ● manually selected model (as model:reasoning).
        if s.is_preset:
            mode = Content.from_markup("[$accent b]◆[/] [b]$e[/]", e=s.effort)
            if s.seen_model:  # the model that last served this preset, from past answers
                mode += Content.from_markup(" [dim]($m)[/]", m=s.seen_model)
        else:
            mode = Content.from_markup("[$accent b]●[/] [b]$m[/]", m=s.model)
            if s.reasoning:
                mode += Content.from_markup("[dim]:[/][b]$r[/]", r=s.reasoning)
        mode += Content.from_markup("  [dim]search[/] $v", v="on" if s.search else "off")
        title = self.session.title or "New chat"
        mode += Content.from_markup("  [dim]│[/]  $t", t=title)
        self.query_one("#status-left", Static).update(mode)
        for welcome in self.query(Welcome):
            welcome.refresh()
        right = Content.from_markup(
            "[dim]$c · $n   ctrl+s history · / commands[/]",
            c=fmt_cost(self.session.total_cost), n=plural(len(self.session.turns), "turn"))
        if self.is_busy:
            right = Content.from_markup("[$warning]esc[/] [dim]cancel[/]   ") + right
        self.query_one("#status-right", Static).update(right)

    def refresh_sidebar(self) -> None:
        lst = self.query_one("#sessions", OptionList)
        lst.clear_options()
        infos = self.store.list()
        for info in infos:
            label = Content.from_markup("$t\n[dim]$a · $n · $c[/]", t=info.title,
                                        a=ago(info.updated_at), n=plural(info.turns, "turn"),
                                        c=fmt_cost(info.cost))
            lst.add_option(Option(label, id=info.id))
        if not infos:
            lst.add_option(Option(Content.from_markup("[dim]No conversations yet[/]"), disabled=True))
        else:
            ids = [i.id for i in infos]
            lst.highlighted = ids.index(self.session.id) if self.session.id in ids else 0

    async def show_session(self, session: Session) -> None:
        self.session = session
        self.query_one(PromptInput).reset_recall()
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        self._links.clear()
        # On the welcome screen the slash menu floats over the chat (see app.tcss)
        # instead of shrinking it, so the centered logo stays put.
        self.query_one("#main").set_class(not session.turns, "-welcome")
        if not session.turns:
            await chat.mount(Welcome(self.settings))
        for t in session.turns:
            text, srcs = for_display(t.assistant, t.sources)
            msg = AssistantMessage(t.model or "", text)
            await chat.mount_all([UserMessage(t.user), msg])
            msg.finish(srcs, fmt_cost(t.cost), self._register_links(srcs))
        chat.scroll_end(animate=False)
        self.refresh_status()
        self.refresh_sidebar()

    def _register_links(self, sources) -> list[int]:
        ids = []
        for s in sources:
            self._links.append(s.url)
            ids.append(len(self._links) - 1)
        return ids

    def action_open_link(self, index: int) -> None:
        if 0 <= index < len(self._links):
            self.open_url(self._links[index])

    # --- prompt ------------------------------------------------------------------

    @on(PromptInput.Changed, "#prompt")
    def _prompt_changed(self, event: PromptInput.Changed) -> None:
        text = event.text_area.text
        menu = self.query_one(CommandMenu)
        if text.startswith("/") and " " not in text and "\n" not in text:
            menu.display = menu.filter(text[1:])
        else:
            menu.display = False

    @on(CommandMenu.OptionSelected, "#menu")
    def _menu_clicked(self, event: CommandMenu.OptionSelected) -> None:
        prompt = self.query_one(PromptInput)
        prompt.text = f"/{event.option.id} "
        prompt.move_cursor(prompt.document.end)
        prompt.focus()

    @on(PromptInput.Submitted)
    async def _submitted(self, event: PromptInput.Submitted) -> None:
        text = event.text.strip()
        prompt = self.query_one(PromptInput)
        self.query_one(CommandMenu).display = False
        if not text:
            return
        if text.startswith("/"):
            prompt.text = ""
            await self.run_command(text)
            return
        if self.is_busy:
            self.notify("Still answering — press esc to cancel.", severity="warning")
            return
        prompt.text = ""
        prompt.reset_recall()
        if self.client is None:
            self._pending = text
            self.ask_for_key()
            return
        self._worker = self.ask(text)

    # --- asking ------------------------------------------------------------------

    @work(exclusive=True, group="chat")
    async def ask(self, question: str) -> None:
        session, settings = self.session, self.settings
        chat = self.query_one("#chat", VerticalScroll)
        for w in chat.query(Welcome):
            await w.remove()
        self.query_one("#main").remove_class("-welcome")
        msg = AssistantMessage(settings.label)
        self.is_busy = True
        try:
            await self._ask(question, session, settings, chat, msg)
        finally:
            self.is_busy = False
            self.refresh_status()

    async def _ask(self, question: str, session: Session, settings: Settings,
                   chat: VerticalScroll, msg: AssistantMessage) -> None:
        await chat.mount_all([UserMessage(question), msg])
        chat.scroll_end(animate=False)
        msg.start()
        self.refresh_status()

        view = StreamingView(msg)
        try:
            done = await turn.arun(self.client.stream_turn(session.input_for(question), settings), view)
        except asyncio.CancelledError:
            await view.stop()
            msg.interrupted()
            raise
        except Exception as e:  # the API failed the turn, or SDK / network errors
            await view.stop()
            err = turn.turn_error(e) or turn.TurnError(f"{type(e).__name__}: {e}")
            msg.fail(err.message)
            self.refresh_status()
            if err.auth:
                self._pending = question
                self.ask_for_key("That key was rejected (401).")
            return
        await view.stop()

        text, srcs = for_display(done.text, done.sources)
        if text != msg.markdown.source:
            await msg.markdown.update(text)
        meta = " · ".join(x for x in (fmt_cost(done.cost), f"{msg.elapsed:.1f}s", done.model) if x)
        msg.finish(srcs, meta, self._register_links(srcs))
        turn.complete(session, settings, question, done)
        try:
            self.store.save(session)
        except OSError as e:
            self.notify(f"Could not save conversation: {e}", severity="error")
        self.refresh_status()
        self.refresh_sidebar()

    def action_cancel(self) -> None:
        if self.query_one(CommandMenu).display:
            self.query_one(CommandMenu).display = False
        elif self.is_busy:
            self._worker.cancel()
        elif self.query_one("#sidebar").display:
            self.action_toggle_sidebar()

    # --- API key -------------------------------------------------------------------

    def ask_for_key(self, reason: str = "") -> None:
        def done(key: str | None) -> None:
            if not key:
                if self.client is None:
                    self.notify("No API key — use /login to add one.", severity="warning")
                return
            try:
                path = auth.set_key(key)
            except (OSError, ValueError) as e:
                self.notify(f"Could not save key: {e}", severity="error")
                return
            self.client = self._client_factory(key)
            self._models = None  # another key may see other models
            self.notify(f"Key saved to {path}")
            if self._pending:
                q, self._pending = self._pending, None
                self._worker = self.ask(q)

        self.push_screen(ApiKeyScreen(reason), done)

    # --- actions & commands ------------------------------------------------------

    async def action_new_chat(self) -> None:
        if self.is_busy:
            self._worker.cancel()
        await self.show_session(Session())
        self.query_one(PromptInput).focus()

    def action_toggle_sidebar(self) -> None:
        bar = self.query_one("#sidebar")
        bar.display = not bar.display
        if bar.display:
            self.refresh_sidebar()
            self.query_one("#sessions").focus()
        else:
            self.query_one(PromptInput).focus()

    @on(OptionList.OptionSelected, "#sessions")
    async def _session_picked(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        if self.is_busy:
            self.notify("Wait for the answer to finish (or press esc).", severity="warning")
            return
        if event.option.id != self.session.id:
            try:
                await self.show_session(self.store.load(event.option.id))
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.notify(f"Could not open conversation: {e}", severity="error")
                return
        self.query_one("#sidebar").display = False
        self.query_one(PromptInput).focus()

    def action_pick_model_effort(self) -> None:
        """Reasoning effort picker for the manually selected model."""
        if not self._pick_model_first(self.action_pick_model_effort):
            self.push_screen(ReasoningScreen(self.settings.model, self.settings.reasoning, self.set_reasoning))

    def _pick_model_first(self, then: Callable[[], None]) -> bool:
        """In preset mode there is no model to set a reasoning effort for: open the model
        list, run `then` once a model is picked, and return True."""
        if not self.settings.is_preset:
            return False
        self.notify("Pick a model first; model effort applies to a specific model.")
        self.action_pick_model(then=then)
        return True

    def action_pick_effort(self) -> None:
        """Effort presets; choosing one drops a manually selected model."""
        def chosen(effort: str | None) -> None:
            if effort:
                self.set_effort(effort)

        self.push_screen(EffortScreen(self.settings.effort, self.settings.seen), chosen)

    def set_effort(self, effort: str) -> None:
        self.settings.use_preset(effort)
        save_state(self.settings, MODE_KEYS)
        self.refresh_status()
        self.notify(f"Effort: {effort}")

    def set_reasoning(self, level: str | None) -> None:
        self.settings.set_reasoning(level)
        save_state(self.settings, MODE_KEYS)
        self.refresh_status()
        self.notify(f"Reasoning: {level or 'model default'}")

    @work(exclusive=True, group="models")
    async def action_pick_model(self, then: Callable[[], None] | None = None) -> None:
        """Model list; `then` runs once a model (not the preset entry) is picked."""
        if self.client is None:
            self.ask_for_key("Set an API key to list models.")
            return
        if self._models is None:
            try:
                self._models = await self.client.list_models()
            except Exception as e:  # SDK/network errors
                msg = api_error_message(e) or f"{type(e).__name__}: {e}"
                self.notify(f"Could not load models. {msg}", severity="error")
                return
        self.push_screen(ModelPalette(self._models, self.settings.model, then))

    def set_model(self, model: str | None) -> None:
        self.settings.use_model(model)
        save_state(self.settings, MODE_KEYS)
        self.refresh_status()
        self.notify(f"Model: {model or 'preset ' + self.settings.effort}")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def run_command(self, line: str) -> None:
        name, _, arg = line[1:].partition(" ")
        command = lookup(name)
        if command is None:
            self.notify(f"Unknown command /{name.lower()}. Type / to see commands.", severity="error")
            return
        result = getattr(self, command.handler)(arg.strip())
        if inspect.isawaitable(result):
            await result

    # One handler per entry in commands.COMMANDS; each gets the text after the command.

    def cmd_quit(self, arg: str) -> None:
        self.exit()

    async def cmd_new(self, arg: str) -> None:
        await self.action_new_chat()

    def cmd_history(self, arg: str) -> None:
        self.action_toggle_sidebar()

    def cmd_effort(self, arg: str) -> None:
        if not arg:
            self.action_pick_model_effort()
            return
        try:
            level = parse_reasoning(arg)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        if not self._pick_model_first(lambda: self.set_reasoning(level)):
            self.set_reasoning(level)

    def cmd_preset(self, arg: str) -> None:
        if arg in EFFORTS:
            self.set_effort(arg)
        elif arg:
            self.notify(f"Unknown effort. Choose: {', '.join(EFFORTS)}", severity="error")
        else:
            self.action_pick_effort()

    def cmd_model(self, arg: str) -> None:
        if arg:
            self.set_model(None if arg in ("off", "none", "preset") else arg)
        else:
            self.action_pick_model()

    def cmd_search(self, arg: str) -> None:
        s = self.settings
        s.search = (arg == "on") if arg in ("on", "off") else not s.search
        save_state(s, ("search",))
        self.refresh_status()
        self.notify(f"Web search {'on' if s.search else 'off'}")

    def cmd_sources(self, arg: str) -> None:
        last = self.session.last
        if not last or not last.sources:
            self.notify("No sources for the last answer.")
            return
        lines = [f"[{x.id}] {x.title or x.url}\n    {x.url}" for x in footer_sources(last.assistant, last.sources)]
        self.notify("\n".join(lines), title="Sources", timeout=15)

    def cmd_copy(self, arg: str) -> None:
        last = self.session.last
        if not last:
            self.notify("Nothing to copy yet.")
            return
        self.copy_to_clipboard(normalize(last.assistant, last.sources))
        self.notify("Copied the last answer.")

    def cmd_rename(self, arg: str) -> None:
        if not arg:
            self.notify("Usage: /rename <title>")
        elif not self.session.turns:
            self.notify("Send a message first.")
        else:
            old, self.session.title = self.session.title, arg
            try:
                self.store.save(self.session)
            except OSError as e:
                self.session.title = old
                self.notify(f"Could not rename: {e}", severity="error")
                return
            self.refresh_status()
            self.refresh_sidebar()

    def cmd_delete(self, arg: str) -> None:
        if not self.session.turns:
            self.notify("Nothing to delete.")
            return

        async def confirmed(yes: bool | None) -> None:
            if yes:
                try:
                    self.store.delete(self.session.id)
                except OSError as e:
                    self.notify(f"Could not delete: {e}", severity="error")
                    return
                if self.is_busy:  # the answer being written belongs to this conversation
                    self._worker.cancel()
                await self.show_session(Session())
                self.notify("Conversation deleted.")

        self.push_screen(ConfirmScreen(f"Delete “{self.session.title}”?"), confirmed)

    def cmd_theme(self, arg: str) -> None:
        self.action_change_theme()

    def cmd_login(self, arg: str) -> None:
        self.ask_for_key()

    def cmd_help(self, arg: str) -> None:
        self.action_help()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        for c in COMMANDS:
            if c.palette:
                title, help = c.palette
                yield SystemCommand(title, help, partial(self.call_later, self.run_command, f"/{c.name}"))
        yield from super().get_system_commands(screen)
