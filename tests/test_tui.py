"""Headless TUI tests with a fake async API client."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from textual.widgets import OptionList

from plex_cli.config import Settings
from plex_cli.session import Session, Store, Turn
from plex_cli.tui import PlexApp
from plex_cli.tui.widgets import AssistantMessage, CommandMenu, PromptInput, UserMessage, Welcome

from test_core import stream


class FakeAsync:
    """Stands in for AsyncClient; records requests and replays canned streams."""

    instances: list["FakeAsync"] = []

    def __init__(self, key, script=None, delay=0.0):
        self.key = key
        self.calls = []
        self.script = script if script is not None else [stream("Hello ", "[web:1]."), stream("Again.")]
        self.delay = delay
        self.models = ["openai/gpt-6-luna", "perplexity/sonar", "xai/grok-4.7"]
        self.model_calls = 0
        FakeAsync.instances.append(self)

    async def list_models(self):
        self.model_calls += 1
        return self.models

    async def stream_turn(self, history, settings):
        from plex_cli.client import parse_stream

        self.calls.append(history)
        for ev in parse_stream(self.script.pop(0)):
            await asyncio.sleep(self.delay)
            yield ev


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("plex_cli.auth.AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr("plex_cli.tui.themes.USER_THEMES_DIR", tmp_path / "themes")
    FakeAsync.instances.clear()


def make_app(tmp_path, key="k", **kw):
    return PlexApp(Settings(), key, store=Store(tmp_path / "sessions"), client_factory=FakeAsync, **kw)


async def send(pilot, text):
    pilot.app.query_one(PromptInput).text = text
    await pilot.press("enter")
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_chat_flow_streams_and_saves(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        assert app.query(Welcome)
        await send(pilot, "first question")
        assert not app.query(Welcome)
        msg = app.query_one(AssistantMessage)
        assert msg.markdown.source == "Hello [1]."  # [web:1] normalized
        assert app.session.title == "first question"
        assert app.store.list()[0].turns == 1

        await send(pilot, "follow up")
        history = FakeAsync.instances[0].calls[1]
        assert [h["content"] for h in history] == ["first question", "Hello [web:1].", "follow up"]
        assert len(app.query(UserMessage)) == 2


@pytest.mark.asyncio
async def test_slash_menu_keeps_welcome_logo_in_place(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        chat, menu, box = app.query_one("#chat"), app.query_one(CommandMenu), app.query_one("#prompt-box")
        before = chat.region
        for keys in (["slash"], ["e"], ["backspace", "backspace"]):
            await pilot.press(*keys)
            await pilot.pause()
            assert chat.region == before  # the menu floats over the welcome screen
        await pilot.press("slash")
        await pilot.pause()
        assert menu.display and menu.region.bottom == box.region.y  # sits right above the prompt

        app.query_one(PromptInput).text = ""
        await pilot.pause()
        await send(pilot, "first question")
        await pilot.press("slash")
        await pilot.pause()
        assert chat.region.bottom == menu.region.y  # in a chat it takes space, as before


@pytest.mark.asyncio
async def test_slash_menu_and_commands(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        prompt = app.query_one(PromptInput)
        prompt.focus()
        await pilot.press("slash", "e", "f")
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.display and menu.selected_command == "effort"
        await pilot.press("backspace")
        await pilot.pause()
        names = [menu.get_option_at_index(i).id for i in range(menu.option_count)]
        assert names[:2] == ["exit", "effort"] and menu.selected_command == "exit"
        await pilot.press("down")
        assert menu.selected_command == names[1]
        await pilot.press("up")
        assert menu.selected_command == names[0]
        assert prompt.text == "/e"  # arrows move the menu, not the cursor/history
        await pilot.press("backspace", "a", "u", "tab")
        assert prompt.text == "/auto-effort "
        await pilot.press("h", "i", "g", "h", "enter")
        await pilot.pause()
        assert app.settings.effort == "high"

        await send(pilot, "/search off")
        assert app.settings.search is False
        await send(pilot, "/model perplexity/sonar")
        assert app.settings.model == "perplexity/sonar"
        await send(pilot, "/model off")
        assert app.settings.model is None


@pytest.mark.asyncio
async def test_model_picker_lists_and_selects(tmp_path):
    from plex_cli.tui.screens import ModelPalette

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "/model")
        assert isinstance(app.screen, ModelPalette)
        await pilot.pause(0.3)
        await pilot.press(*"sonar")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause()
        assert app.settings.model == "perplexity/sonar"

        # Reopens on the current model, from the cached list; the preset entry is first.
        await send(pilot, "/model")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ModelPalette)
        options = app.screen.query_one("CommandList").options
        assert options[0].hit.text == "preset"
        assert options[app.screen.query_one("CommandList").highlighted].hit.text == "perplexity/sonar"
        await pilot.press("up", "up", "enter")
        await pilot.pause()
        assert app.settings.model is None
        assert FakeAsync.instances[0].model_calls == 1

        # Enter on /model in the command menu opens the list right away, like /quit.
        await pilot.press(*"/mod")
        await pilot.pause()
        assert app.query_one(CommandMenu).display
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        assert isinstance(app.screen, ModelPalette)


@pytest.mark.asyncio
async def test_model_picker_reports_errors(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        async def boom():
            raise RuntimeError("offline")
        FakeAsync.instances[0].list_models = boom
        await send(pilot, "/model")
        assert not app.screen.__class__.__name__ == "ModelPalette"
        assert any("offline" in str(n.message) for n in app._notifications)


@pytest.mark.asyncio
async def test_history_sidebar_loads_session(tmp_path):
    store = Store(tmp_path / "sessions")
    old = Session()
    old.commit(Turn("old question", "old answer", {}, 0.001))
    store.save(old)
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.query_one("#sidebar").display
        await pilot.press("enter")
        await pilot.pause()
        assert app.session.id == old.id
        assert app.query_one(UserMessage)
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app.session.turns == [] and app.query(Welcome)


@pytest.mark.asyncio
async def test_cancel_with_escape_keeps_history_clean(tmp_path):
    app = PlexApp(Settings(), "k", store=Store(tmp_path / "s"),
                  client_factory=lambda k: FakeAsync(k, script=[stream(*["x"] * 50)], delay=0.05))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(PromptInput).text = "long"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.is_busy
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.is_busy
        assert app.session.turns == []
        assert "interrupted" in str(app.query_one(".meta").render())


@pytest.mark.asyncio
async def test_failure_shows_error(tmp_path):
    bad = [[NS(type="response.failed", error=NS(message="boom"))]]
    app = PlexApp(Settings(), "k", store=Store(tmp_path / "s"),
                  client_factory=lambda k: FakeAsync(k, script=bad))
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "q")
        assert app.query_one(AssistantMessage).has_class("failed")
        assert app.session.turns == []


@pytest.mark.asyncio
async def test_missing_key_prompts_and_saves(tmp_path):
    from plex_cli import auth

    app = make_app(tmp_path, key=None)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert type(app.screen).__name__ == "ApiKeyScreen"
        for ch in "pplx-test-key":
            await pilot.press(ch if ch != "-" else "minus")
        await pilot.press("enter")
        await pilot.pause()
        assert auth.get_key(tmp_path / "auth.json") == "pplx-test-key"
        assert app.client is not None


@pytest.mark.asyncio
async def test_exit_command_quits(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        pilot.app.query_one(PromptInput).text = "/exit"
        await pilot.press("enter")
        await pilot.pause()
    assert app.return_code == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/new", "/clear"])
async def test_new_and_clear_start_a_fresh_conversation(tmp_path, command):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "first question")
        old = app.session
        assert old.turns
        await send(pilot, command)
        assert app.session.id != old.id
        assert app.session.turns == [] and app.query(Welcome)
        assert not app.query(UserMessage)


@pytest.mark.asyncio
async def test_clear_is_suggested_in_command_menu(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        pilot.app.query_one(PromptInput).text = "/cl"
        await pilot.pause()
        assert app.query_one(CommandMenu).selected_command == "clear"


@pytest.mark.asyncio
async def test_theme_picker_previews_and_reverts(tmp_path, monkeypatch):
    from plex_cli.tui.screens import ThemePalette

    from plex_cli.config import load_state

    saved = lambda: load_state().get("theme")  # noqa: E731
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        start = app.theme
        app.action_change_theme()
        await pilot.pause()
        assert isinstance(app.screen, ThemePalette)
        assert app.theme == start  # opens on the current theme
        await pilot.press("down")
        await pilot.pause()
        previewed = app.theme
        assert previewed != start
        await pilot.press("down")
        await pilot.pause()
        assert app.theme not in (start, previewed)
        assert saved() is None  # nothing persisted while previewing

        await pilot.press("escape")
        await pilot.pause()
        assert app.theme == start
        assert saved() is None

        app.action_change_theme()
        await pilot.pause()
        await pilot.press("down", "down", "down")
        await pilot.pause()
        chosen = app.theme
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ThemePalette)
        assert app.theme == chosen != start
        assert saved() == chosen


@pytest.mark.asyncio
async def test_only_theme_files_offered(tmp_path):
    from plex_cli.tui.themes import BUNDLED_THEMES_DIR

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        assert set(app.available_themes) == {p.stem for p in BUNDLED_THEMES_DIR.glob("*.toml")}
        names = list(app.available_themes)
        assert names[:4] == ["terminal", "terminal-coral", "terminal-green", "terminal-violet"]
        assert names[-1] == "matrix"  # effect themes last
        assert app.theme == "terminal"  # the default
        app.theme = "terminal-coral"
        await pilot.pause()
        assert app.native_ansi_color


def test_user_themes_add_override_and_report_problems(tmp_path):
    from plex_cli.tui.themes import load_themes

    (tmp_path / "mine.toml").write_text('terminal = true\naccent = "#00ff88"\n')
    (tmp_path / "dracula.toml").write_text('primary = "#ff0000"\n')
    (tmp_path / "typo.toml").write_text('primery = "#ff0000"\n')
    (tmp_path / "badcolor.toml").write_text('primary = "not-a-color"\n')
    (tmp_path / "broken.toml").write_text('primary = \n')

    themes, problems = load_themes(tmp_path)
    by_name = {t.name: t for t in themes}
    assert by_name["mine"].ansi and by_name["mine"].accent == "#00ff88"
    assert by_name["mine"].variables["block-cursor-foreground"] == "#00ff88"
    assert by_name["dracula"].primary == "#ff0000"
    assert {"typo", "badcolor", "broken"}.isdisjoint(by_name)
    assert sorted(p.split(":")[0].rsplit("/", 1)[1] for p in problems) == \
        ["badcolor.toml", "broken.toml", "typo.toml"]
    assert "primery" in next(p for p in problems if "typo" in p)


@pytest.mark.asyncio
async def test_matrix_theme_runs_rain_only_while_selected(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        assert not app.rain.active
        app.theme = "matrix"
        await pilot.pause(0.5)
        assert app.rain.active
        welcome = app.query_one(Welcome)
        lines = welcome.render_lines(welcome.region.reset_offset)
        text = "".join(line.text for line in lines)
        assert "Ask anything" in text  # text stays readable
        assert any(ch in text for ch in "ｱｲｳｴｵｶｷ0123456789")  # rain around it

        app.theme = "terminal-coral"
        await pilot.pause()
        assert not app.rain.active
        assert not any(ch in "".join(l.text for l in welcome.render_lines(welcome.region.reset_offset))
                       for ch in "ｱｳｴｵｶｷ")


def test_user_themes_dir_is_created_with_placeholder(tmp_path, monkeypatch):
    from plex_cli.tui import themes as mod

    folder = tmp_path / "cfg" / "plex" / "themes"
    monkeypatch.setattr(mod, "USER_THEMES_DIR", folder)
    themes, problems = mod.load_themes()
    placeholder = folder / mod.USER_THEMES_PLACEHOLDER
    assert placeholder.is_file() and placeholder.read_bytes() == b""
    assert not problems and "terminal" in {t.name for t in themes}

    (folder / "mine.toml").write_text('terminal = true\naccent = "#00ff88"\n')
    placeholder.unlink()
    themes, problems = mod.load_themes()
    assert "mine" in {t.name for t in themes} and "dracula" in {t.name for t in themes}
    assert not placeholder.exists()  # not recreated once the folder exists


def test_unknown_effect_is_reported(tmp_path):
    from plex_cli.tui.themes import load_themes

    (tmp_path / "fx.toml").write_text('terminal = true\neffect = "snow"\n')
    themes, problems = load_themes(tmp_path)
    assert "fx" not in {t.name for t in themes}
    assert "unknown effect 'snow'" in problems[0]


@pytest.mark.asyncio
async def test_theme_change_recolors_existing_text(tmp_path):
    from textual.widgets import Static

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "q")
        who = app.query_one(".who", Static)

        def accent_color():
            line = who.render_lines(who.region.reset_offset)[0]
            return next(str(seg.style.color.triplet.hex) for seg in line if "plex" in seg.text)

        app.theme = "terminal-coral"
        await pilot.pause()
        assert accent_color() == "#d97757"
        app.theme = "terminal-green"
        await pilot.pause()
        assert accent_color() == "#00ff41"


@pytest.mark.asyncio
async def test_effort_picker_shows_seen_models(tmp_path):
    from plex_cli.tui.screens import EffortScreen

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "hi")  # preset mode: the answer reports fast → gpt-6-luna
        assert app.settings.seen["fast"]["model"] == "openai/gpt-6-luna"
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert isinstance(app.screen, EffortScreen)
        opts = app.screen.query_one(OptionList)
        assert "openai/gpt-6-luna" in str(opts.get_option("fast").prompt)
        assert "after first use" in str(opts.get_option("low").prompt)
        await pilot.press("escape")

        await send(pilot, "/model perplexity/sonar")
        await send(pilot, "again")  # direct-model answers are not attributed to an effort level
        assert app.settings.seen["fast"]["model"] == "openai/gpt-6-luna"


@pytest.mark.asyncio
async def test_reasoning_effort_for_selected_model(tmp_path):
    from plex_cli.tui.screens import ReasoningScreen

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "/model perplexity/sonar")
        await send(pilot, "/effort high")
        assert (app.settings.model, app.settings.reasoning) == ("perplexity/sonar", "high")
        assert "● perplexity/sonar:high" in str(app.query_one("#status-left").render())

        await send(pilot, "/effort")
        assert isinstance(app.screen, ReasoningScreen)
        await pilot.press("escape")

        await send(pilot, "/effort default")
        assert app.settings.reasoning is None

        await send(pilot, "/effort max")
        await send(pilot, "/model xai/grok-4.7")  # switching model drops the level
        assert app.settings.reasoning is None
        await send(pilot, "/auto-effort ultra")  # a preset drops the model
        assert app.settings.model is None and app.settings.effort == "ultra"
        assert "◆ ultra" in str(app.query_one("#status-left").render())


@pytest.mark.asyncio
async def test_model_effort_in_preset_mode_picks_a_model_first(tmp_path):
    from plex_cli.tui.screens import ModelPalette, ReasoningScreen

    async def pick(pilot, query):
        await pilot.pause(0.3)
        assert isinstance(pilot.app.screen, ModelPalette)
        await pilot.press(*query)
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause()

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        # With a level: pick a model, then the level applies to it.
        await send(pilot, "/effort high")
        await pick(pilot, "sonar")
        assert (app.settings.model, app.settings.reasoning) == ("perplexity/sonar", "high")

        # Without a level: pick a model, then the effort picker opens.
        await send(pilot, "/auto-effort low")
        await send(pilot, "/effort")
        await pick(pilot, "grok")
        assert app.settings.model == "xai/grok-4.7"
        assert isinstance(app.screen, ReasoningScreen)
        await pilot.press("escape")

        # Going back to presets or closing the list sets nothing.
        await send(pilot, "/auto-effort low")
        await send(pilot, "/effort max")
        await pick(pilot, "preset")
        assert app.settings.model is None and app.settings.reasoning is None
        await send(pilot, "/effort max")
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause()
        assert app.settings.model is None and app.settings.reasoning is None

        await send(pilot, "/effort bogus")  # rejected before any picker opens
        assert not isinstance(app.screen, ModelPalette)


@pytest.mark.asyncio
async def test_welcome_line_follows_mode(tmp_path):
    from plex_cli.tui.widgets import Welcome

    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        welcome = lambda: str(app.query_one(Welcome).render())
        assert "effort fast" in welcome()
        await send(pilot, "/model perplexity/sonar")
        await send(pilot, "/effort high")
        assert "model perplexity/sonar:high" in welcome() and "effort" not in welcome().split("\n")[-1]
        await send(pilot, "/auto-effort low")
        assert "effort low" in welcome()


@pytest.mark.asyncio
async def test_delete_while_answering_cancels_the_answer(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "first")
        sid = app.session.id
        fake = FakeAsync.instances[0]
        fake.script, fake.delay = [stream("a ", "b ", "c ", "d")], 0.2
        app.query_one(PromptInput).text = "second"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.is_busy
        app.query_one(PromptInput).text = "/delete"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.return_code is None  # still running, no crash
        assert not app.store.path(sid).exists()
        assert app.session.turns == [] and app.query(Welcome)


def test_every_command_has_a_handler():
    from plex_cli.tui.commands import COMMANDS, lookup

    for c in COMMANDS:
        assert callable(getattr(PlexApp, c.handler, None)), c.handler
        for name in (c.name, *c.aliases):
            assert lookup(name) is c
            assert lookup(name.upper()) is c
    assert lookup("nope") is None


@pytest.mark.asyncio
async def test_aliases_and_palette_run_commands(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "first question")
        await send(pilot, "/CLEAR")
        assert app.session.turns == []
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert {"New chat", "Toggle web search", "Set API key", "Help"} <= set(titles)
        search = next(c for c in app.get_system_commands(app.screen) if c.title == "Toggle web search")
        search.callback()
        await pilot.pause()
        assert app.settings.search is False
        await send(pilot, "/q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_up_and_down_recall_past_messages(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await send(pilot, "one")
        await send(pilot, "two")
        prompt = app.query_one(PromptInput)
        await pilot.press("up")
        assert prompt.text == "two"
        await pilot.press("up", "up")
        assert prompt.text == "one"  # stops at the oldest
        await pilot.press("down")
        assert prompt.text == "two"
        await pilot.press("down")
        assert prompt.text == ""
        await pilot.press("up", "x")  # typing ends browsing; arrows move the cursor again
        await pilot.press("up")
        assert prompt.text == "twox"
        await pilot.press("ctrl+n")
        await pilot.pause()
        prompt.text = ""
        await pilot.press("up")
        assert prompt.text == ""  # a new chat has nothing to recall
