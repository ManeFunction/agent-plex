"""The plex full-screen chat app."""

from __future__ import annotations

import asyncio
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
from ..citations import footer_sources, normalize
from .. import turn
from ..client import AsyncClient, Source, api_error_message
from ..config import DEFAULT_THEME, EFFORTS, REASONING, SESSIONS_DIR, Settings, save_state
from ..render import fmt_cost, plural
from ..session import Session, Store, ago
from .screens import (DEFAULT_REASONING, ApiKeyScreen, ConfirmScreen, EffortScreen, HelpScreen,
                      ModelPalette, ReasoningScreen, ThemePalette)
from .effects import BackdropScroll, BackdropVertical, RainField
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
        Binding("ctrl+e", "pick_effort", "Auto effort", priority=True),
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
        self._busy = False
        self._links: list[str] = []
        self._recall_index: int | None = None
        self._pending: str | None = None  # question waiting for an API key
        self._models: list[str] | None = None  # /v1/models, fetched on first /model
        self.rain = RainField()  # drawn by the background widgets when the theme asks for it
        self._rain_timer = None

    # --- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static(Content.from_markup("[b]History[/]  [dim]ctrl+s[/]"), id="sidebar-title")
                yield OptionList(id="sessions")
            with BackdropVertical(id="main"):
                yield BackdropScroll(id="chat")
                yield CommandMenu(id="menu")
                with Vertical(id="prompt-box"):
                    yield PromptInput(id="prompt", placeholder="Ask anything…  (/ for commands)")
                with Horizontal(id="statusbar"):
                    yield Static(id="status-left")
                    yield Static(id="status-right")

    async def on_mount(self) -> None:
        self._rain_timer = self.set_interval(1 / RainField.FPS, self._rain_tick, pause=True)
        self.setup_themes()
        self.apply_effect()
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        prompt = self.query_one(PromptInput)
        prompt.menu = self.query_one(CommandMenu)
        self.query_one(CommandMenu).display = False
        self.query_one("#sidebar").display = False
        self.query_one("#chat").anchor()
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

    def apply_effect(self) -> None:
        """Start or stop the theme's animated background."""
        active = getattr(self.current_theme, "effect", None) == "rain"
        if active == self.rain.active:
            return
        self.rain.active = active
        if active:
            self.rain.resize(*self.size)
        self._sync_rain_timer()
        self._refresh_backdrops()

    def _sync_rain_timer(self) -> None:
        # Animate only while the rain is on and the terminal window has focus.
        if self.rain.active and self.app_focus:
            self._rain_timer.resume()
        else:
            self._rain_timer.pause()

    def watch_app_focus(self, focused: bool) -> None:
        if self._rain_timer is not None:
            self._sync_rain_timer()

    def _rain_tick(self) -> None:
        self.rain.resize(*self.size)
        self.rain.tick()
        self._refresh_backdrops()

    def _refresh_backdrops(self) -> None:
        main = self.screen_stack[0]  # the chat screen, even while a dialog is open
        for widget in main.query("#main, #chat, Welcome"):
            widget.refresh()

    def _on_theme_changed(self, theme) -> None:
        # Text styled with theme colors ($accent etc.) keeps the old colors until redrawn.
        for widget in self.screen_stack[0].query("*"):
            widget.refresh()
        self.apply_effect()
        if isinstance(self.screen, ThemePalette):
            return  # previewing; saved once the picker closes
        self.save_theme()

    def save_theme(self) -> None:
        if self.settings.theme != self.theme:
            self.settings.theme = self.theme
            save_state(self.settings)

    def action_change_theme(self) -> None:
        self.push_screen(ThemePalette(), lambda _: self.save_theme())

    # --- status / sidebar ------------------------------------------------------

    def refresh_status(self) -> None:
        s = self.settings
        # Distinct glyphs: ◆ effort preset, ● manually selected model (as model:reasoning).
        if s.model:
            mode = Content.from_markup("[$accent b]●[/] [b]$m[/]", m=s.model)
            if s.reasoning:
                mode += Content.from_markup("[dim]:[/][b]$r[/]", r=s.reasoning)
        else:
            mode = Content.from_markup("[$accent b]◆[/] [b]$e[/]", e=s.effort)
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
        if infos:
            ids = [i.id for i in infos]
            lst.highlighted = ids.index(self.session.id) if self.session.id in ids else 0

    async def show_session(self, session: Session) -> None:
        self.session = session
        self._recall_index = None
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        self._links.clear()
        # On the welcome screen the slash menu floats over the chat (see app.tcss)
        # instead of shrinking it, so the centered logo stays put.
        self.query_one("#main").set_class(not session.turns, "-welcome")
        if not session.turns:
            await chat.mount(Welcome(self.settings))
        for t in session.turns:
            msg = AssistantMessage(t.model or "", normalize(t.assistant, t.sources))
            await chat.mount_all([UserMessage(t.user), msg])
            srcs = footer_sources(t.assistant, t.sources)
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

    @on(PromptInput.HistoryRequest)
    def _recall(self, event: PromptInput.HistoryRequest) -> None:
        past = [t.user for t in self.session.turns]
        if not past:
            return
        prompt = self.query_one(PromptInput)
        if self._recall_index is None:
            if event.direction > 0:
                return
            self._recall_index = len(past)
        self._recall_index += event.direction
        if self._recall_index >= len(past):
            self._recall_index = None
            prompt.text = ""
            prompt._recalling = False
            return
        self._recall_index = max(0, self._recall_index)
        prompt.text = past[self._recall_index]
        prompt.move_cursor(prompt.document.end)
        prompt._recalling = True

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
        self._recall_index = None
        prompt._recalling = False
        if self.client is None:
            self._pending = text
            self.ask_for_key()
            return
        self._worker = self.ask(text)

    # --- asking ------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

    @work(exclusive=True, group="chat")
    async def ask(self, question: str) -> None:
        session, settings = self.session, self.settings
        chat = self.query_one("#chat", VerticalScroll)
        for w in chat.query(Welcome):
            await w.remove()
        self.query_one("#main").remove_class("-welcome")
        msg = AssistantMessage(settings.label)
        self._busy = True
        try:
            await self._ask(question, session, settings, chat, msg)
        finally:
            self._busy = False
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

        text = normalize(done.text, done.sources)
        if text != msg.markdown.source:
            await msg.markdown.update(text)
        srcs = footer_sources(text, done.sources)
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
            except (OSError, ValueError, KeyError) as e:
                self.notify(f"Could not open conversation: {e}", severity="error")
                return
        self.query_one("#sidebar").display = False
        self.query_one(PromptInput).focus()

    def action_pick_model_effort(self, level: str | None = None) -> None:
        """Reasoning effort for the manually selected model; `level` skips the picker.

        In preset mode there is no model to set it for, so a model is picked first.
        """
        if not self.settings.model:
            self.notify("Pick a model first; model effort applies to a specific model.")
            self.action_pick_model(then=lambda: self.action_pick_model_effort(level))
            return
        if level:
            self.set_reasoning(None if level == DEFAULT_REASONING else level)
            return

        def picked(level: str | None) -> None:
            if level:
                self.set_reasoning(None if level == DEFAULT_REASONING else level)

        self.push_screen(ReasoningScreen(self.settings.model, self.settings.reasoning), picked)

    def action_pick_effort(self) -> None:
        """Effort presets; choosing one drops a manually selected model."""
        def chosen(effort: str | None) -> None:
            if effort:
                self.set_effort(effort)

        self.push_screen(EffortScreen(self.settings.effort, self.settings.seen), chosen)

    def set_effort(self, effort: str) -> None:
        self.settings.effort, self.settings.model = effort, None
        save_state(self.settings)
        self.refresh_status()
        self.notify(f"Effort: {effort}")

    def set_reasoning(self, level: str | None) -> None:
        self.settings.reasoning = level
        save_state(self.settings)
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
        if model != self.settings.model:
            self.settings.reasoning = None  # not every model supports every level
        self.settings.model = model
        save_state(self.settings)
        self.refresh_status()
        self.notify(f"Model: {model or 'preset ' + self.settings.effort}")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def run_command(self, line: str) -> None:
        cmd, _, arg = line[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        s = self.settings
        if cmd in ("quit", "exit", "q"):
            self.exit()
        elif cmd in ("new", "clear"):
            await self.action_new_chat()
        elif cmd == "history":
            self.action_toggle_sidebar()
        elif cmd == "effort":
            if arg and arg not in REASONING and arg != DEFAULT_REASONING:
                self.notify(f"Unknown model effort. Choose: {', '.join(REASONING)}, default",
                            severity="error")
            else:
                self.action_pick_model_effort(arg or None)
        elif cmd == "auto-effort":
            if arg in EFFORTS:
                self.set_effort(arg)
            elif arg:
                self.notify(f"Unknown effort. Choose: {', '.join(EFFORTS)}", severity="error")
            else:
                self.action_pick_effort()
        elif cmd == "model":
            if arg:
                self.set_model(None if arg in ("off", "none", "preset") else arg)
            else:
                self.action_pick_model()
        elif cmd == "search":
            s.search = (arg == "on") if arg in ("on", "off") else not s.search
            save_state(s)
            self.refresh_status()
            self.notify(f"Web search {'on' if s.search else 'off'}")
        elif cmd == "sources":
            last = self.session.last
            if not last or not last.sources:
                self.notify("No sources for the last answer.")
            else:
                lines = [f"[{x.id}] {x.title or x.url}\n    {x.url}"
                         for x in footer_sources(last.assistant, last.sources)]
                self.notify("\n".join(lines), title="Sources", timeout=15)
        elif cmd == "copy":
            last = self.session.last
            if not last:
                self.notify("Nothing to copy yet.")
            else:
                self.copy_to_clipboard(normalize(last.assistant, last.sources))
                self.notify("Copied the last answer.")
        elif cmd == "rename":
            if not arg:
                self.notify("Usage: /rename <title>")
            elif not self.session.turns:
                self.notify("Send a message first.")
            else:
                self.session.title = arg
                self.store.save(self.session)
                self.refresh_status()
                self.refresh_sidebar()
        elif cmd == "delete":
            if not self.session.turns:
                self.notify("Nothing to delete.")
                return

            async def confirmed(yes: bool | None) -> None:
                if yes:
                    if self.is_busy:  # the answer being written belongs to this conversation
                        self._worker.cancel()
                    self.store.delete(self.session.id)
                    await self.show_session(Session())
                    self.notify("Conversation deleted.")

            self.push_screen(ConfirmScreen(f"Delete “{self.session.title}”?"), confirmed)
        elif cmd == "theme":
            self.action_change_theme()
        elif cmd in ("login", "key"):
            self.ask_for_key()
        elif cmd == "help":
            self.action_help()
        else:
            self.notify(f"Unknown command /{cmd}. Type / to see commands.", severity="error")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield SystemCommand("New chat", "Start a new conversation", self.action_new_chat)
        yield SystemCommand("History", "Toggle the conversation list", self.action_toggle_sidebar)
        yield SystemCommand("Auto effort", "Change research effort preset", self.action_pick_effort)
        yield SystemCommand("Model", "Pick a specific model", self.action_pick_model)
        yield SystemCommand("Effort", "Reasoning effort for the selected model",
                            self.action_pick_model_effort)
        yield SystemCommand("Toggle web search", "Turn web search on or off",
                            lambda: self.call_later(self.run_command, "/search"))
        yield SystemCommand("Set API key", "Store a Perplexity API key", self.ask_for_key)
        yield SystemCommand("Help", "Keys and commands", self.action_help)
        yield from super().get_system_commands(screen)
