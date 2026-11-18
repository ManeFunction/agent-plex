from types import SimpleNamespace as NS

import pytest

from plex_cli import session as sess
from plex_cli.citations import cited_ids, footer_sources, normalize
from plex_cli.cli import run_turn
from plex_cli.client import (
    Client, Done, Searching, Source, SourcesFound, TextDelta, TurnFailed,
    build_request, parse_stream,
)
from plex_cli.config import ConfigError, Settings, resolve_api_key, settings_from
from plex_cli.render import Renderer


def result(i, url="https://x", title="T"):
    return NS(id=i, url=f"{url}/{i}", title=f"{title}{i}", snippet="", date=None)


def completed(text="Answer [1].", results=(1, 2), cost=0.0021, rid="resp_1"):
    return NS(
        type="response.completed",
        response=NS(
            id=rid, model="openai/gpt-6-luna", effort="fast@2026-11-11.0", status="completed",
            output=[
                NS(type="search_results", results=[result(i) for i in results], queries=["q"]),
                NS(type="message", content=[NS(type="output_text", text=text)]),
            ],
            usage=NS(input_tokens=10, output_tokens=5, cost=NS(total_cost=cost)),
        ),
    )


def stream(*deltas, final=None):
    evs = [NS(type="response.created", response=NS(id="resp_1"))]
    evs.append(NS(type="response.reasoning.search_queries", queries=["python release"]))
    evs.append(NS(type="response.reasoning.search_results", results=[result(1), result(2)]))
    evs += [NS(type="response.output_text.delta", delta=d) for d in deltas]
    evs.append(final or completed("".join(deltas)))
    return evs


# --- request building -------------------------------------------------------

def test_request_effort_mode():
    req = build_request([{"role": "user"}], Settings(effort="low"))
    assert req["extra_body"] == {"effort": "low"}
    assert "model" not in req and "tools" not in req
    assert req["stream"] is True


def test_request_effort_no_search():
    req = build_request([], Settings(search=False))
    assert req["extra_body"] == {"effort": "fast", "max_tool_calls": 0}


def test_request_model_mode():
    req = build_request([], Settings(model="perplexity/sonar", max_steps=5))
    assert req["model"] == "perplexity/sonar"
    assert req["tools"] == [{"type": "web_search"}]
    assert req["max_steps"] == 5
    assert "extra_body" not in req
    assert "tools" not in build_request([], Settings(model="m", search=False))


# --- stream parsing ---------------------------------------------------------

def test_parse_stream_happy_path():
    evs = list(parse_stream(stream("Hello ", "[1].")))
    assert isinstance(evs[0], Searching) and evs[0].queries == ["python release"]
    assert isinstance(evs[1], SourcesFound) and [s.id for s in evs[1].sources] == [1, 2]
    assert [e.text for e in evs if isinstance(e, TextDelta)] == ["Hello ", "[1]."]
    done = evs[-1]
    assert isinstance(done, Done)
    assert done.text == "Hello [1]."
    assert done.cost == pytest.approx(0.0021)
    assert done.response_id == "resp_1"
    assert set(done.sources) == {1, 2}


def test_parse_stream_output_item_sources_not_duplicated():
    evs = stream("x")
    evs.insert(3, NS(type="response.output_item.added",
                     item=NS(type="search_results", results=[result(1), result(3)])))
    found = [e for e in parse_stream(evs) if isinstance(e, SourcesFound)]
    assert [[s.id for s in f.sources] for f in found] == [[1, 2], [3]]


def test_parse_stream_failed():
    evs = [NS(type="response.output_text.delta", delta="par"),
           NS(type="response.failed", error=NS(message="boom", code=None))]
    with pytest.raises(TurnFailed, match="boom"):
        list(parse_stream(evs))


def test_parse_stream_without_completed_returns_partial():
    evs = [NS(type="response.output_text.delta", delta="abc")]
    done = list(parse_stream(evs))[-1]
    assert done.text == "abc" and done.cost is None


def test_parse_stream_accepts_dicts_and_unknown_events():
    evs = [{"type": "response.something_new"},
           {"type": "response.output_text.delta", "delta": "hi"},
           {"type": "response.completed", "response": {"id": "r", "output": [], "usage": None}}]
    done = list(parse_stream(evs))[-1]
    assert done.text == "hi" and done.cost is None


# --- citations --------------------------------------------------------------

def test_citations():
    srcs = {1: Source(1, "u1"), 2: Source(2, "u2"), 3: Source(3, "u3")}
    text = "A [web:2] b [1] c [2] d [9] e [link](x)"
    assert cited_ids(text) == [2, 1, 9]
    assert normalize(text, srcs) == "A [2] b [1] c [2] d [9] e [link](x)"
    assert [s.id for s in footer_sources(text, srcs)] == [2, 1]
    assert [s.id for s in footer_sources("no cites", srcs)] == [1, 2, 3]


# --- session ----------------------------------------------------------------

def test_session_input_and_trim():
    s = sess.Session(max_turns=2)
    for i in range(3):
        s.commit(sess.Turn(f"q{i}", f"a{i}", cost=0.01))
    items = s.input_for("next")
    assert [i["content"] for i in items] == ["q1", "a1", "q2", "a2", "next"]
    assert [i["role"] for i in items] == ["user", "assistant"] * 2 + ["user"]
    assert all(i["type"] == "message" for i in items)
    assert s.total_cost == pytest.approx(0.03)




# --- config -----------------------------------------------------------------

def test_api_key_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr("plex_cli.auth.AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr("plex_cli.auth.get_key", lambda path=None: None)
    assert resolve_api_key({}) is None
    assert resolve_api_key({"api_key": "file"}) == "file"
    monkeypatch.setenv("PERPLEXITY_API_KEY", "env")
    assert resolve_api_key({"api_key": "file"}) == "env"


def test_auth_status_reports_the_key_actually_used(monkeypatch, tmp_path, capsys):
    from plex_cli import auth
    from plex_cli.cli import main
    from plex_cli.config import api_key_source

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr("plex_cli.auth.AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr("plex_cli.cli.load_file_config", lambda: {"api_key": "pplx-from-config-1234"})
    assert api_key_source({"api_key": "c"}) == ("c", "config")
    assert main(["auth", "status"]) == 0
    assert "config.toml" in capsys.readouterr().out
    auth.set_key("pplx-stored-key-5678")
    assert api_key_source({"api_key": "c"}) == ("pplx-stored-key-5678", "auth")
    main(["auth", "status"])
    assert "stored key" in capsys.readouterr().out
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-env-key-9999")
    assert api_key_source({"api_key": "c"})[1] == "env"
    main(["auth", "status"])
    assert "environment" in capsys.readouterr().out


def test_settings_from_file():
    s = settings_from({"effort": "high", "search": False})
    assert s.effort == "high" and not s.search
    with pytest.raises(ConfigError):
        settings_from({"effort": "turbo"})


# --- REPL turn & commands ---------------------------------------------------

class FakeCreate:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        item = self.streams.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(item)


def test_run_turn_commits_and_replays_history(capsys):
    fake = FakeCreate(stream("First [1]."), stream("Second."))
    client, s, r = Client("k", create=fake), sess.Session(), Renderer(raw=True)
    assert run_turn(client, s, Settings(), r, "q1")
    assert run_turn(client, s, Settings(), r, "q2")
    assert [t.user for t in s.turns] == ["q1", "q2"]
    second_input = fake.calls[1]["input"]
    assert [i["content"] for i in second_input] == ["q1", "First [1].", "q2"]
    out = capsys.readouterr().out
    assert "First [1]." in out and "Sources:" in out and "https://x/1" in out


def test_run_turn_failure_not_committed():
    fake = FakeCreate([NS(type="response.failed", error=NS(message="nope"))])
    s = sess.Session()
    assert not run_turn(Client("k", create=fake), s, Settings(), Renderer(raw=True), "q")
    assert s.turns == []


def test_run_turn_api_errors(capsys):
    import httpx
    import perplexity

    req = httpx.Request("POST", "https://api.perplexity.ai/v1/agent")
    errs = [
        perplexity.AuthenticationError("bad", response=httpx.Response(401, request=req), body=None),
        perplexity.APIStatusError("pay", response=httpx.Response(402, request=req), body=None),
        perplexity.APIConnectionError(request=req),
    ]
    s = sess.Session()
    for e in errs:
        assert not run_turn(Client("k", create=FakeCreate(e)), s, Settings(), Renderer(raw=True), "q")
    err = capsys.readouterr().err
    assert "401" in err and "402" in err and "Connection error" in err
    assert s.turns == []


def test_run_turn_keyboard_interrupt():
    def gen():
        yield NS(type="response.output_text.delta", delta="part")
        raise KeyboardInterrupt

    s = sess.Session()
    client = Client("k", create=lambda **kw: gen())
    assert not run_turn(client, s, Settings(), Renderer(raw=True), "q")
    assert s.turns == []



# --- models seen per effort level ------------------------------------------------

def test_done_carries_effort_version():
    done = list(parse_stream(stream("Hi")))[-1]
    assert done.effort == "fast@2026-11-11.0" and done.model == "openai/gpt-6-luna"


def test_note_seen_and_state_round_trip(tmp_path):
    from plex_cli.config import load_state, save_seen, save_state

    s = Settings()
    assert s.note_seen("high@2026-10-28.0", "openai/gpt-6.1-terra", now=100)
    assert s.seen["high"] == {"model": "openai/gpt-6.1-terra", "version": "2026-10-28.0", "at": 100}
    assert not s.note_seen("bogus@1", "x") and not s.note_seen(None, "x") and not s.note_seen("low", None)

    p = tmp_path / "state.json"
    save_seen(s.seen, p)
    assert settings_from({}, load_state(p)).seen == s.seen
    # Saving `seen` keeps the saved effort, and saving settings keeps `seen`.
    save_state(Settings(effort="medium"), path=p)
    assert load_state(p)["seen"] == s.seen
    save_seen(s.seen, p)
    state = load_state(p)
    assert state["effort"] == "medium" and state["seen"] == s.seen
    # Junk in the state file is dropped.
    bad = {"seen": {"fast": {"model": 1, "at": 1}, "nope": {"model": "m", "at": 1}, "low": "x"}}
    assert settings_from({}, bad).seen == {}


# --- reasoning effort for a manually selected model --------------------------------

def test_request_model_reasoning():
    req = build_request([], Settings(model="openai/gpt-6-luna", reasoning="high"))
    assert req["extra_body"] == {"reasoning": {"effort": "high"}}
    assert "reasoning" not in build_request([], Settings(model="perplexity/sonar")).get("extra_body", {})
    # Ignored in preset mode.
    assert build_request([], Settings(reasoning="high"))["extra_body"] == {"effort": "fast"}


def test_label_model_reasoning():
    assert Settings(model="perplexity/sonar", reasoning="low").label == "perplexity/sonar:low"
    assert Settings(model="perplexity/sonar").label == "perplexity/sonar"
    assert Settings(effort="high", reasoning="low").label == "high"


# --- the shared turn module ---------------------------------------------------------

def test_turn_error_classifies_failures():
    import httpx
    import perplexity

    from plex_cli.turn import turn_error

    req = httpx.Request("POST", "https://api.perplexity.ai/v1/agent")
    auth = turn_error(perplexity.AuthenticationError("bad", response=httpx.Response(401, request=req), body=None))
    assert auth.auth and "401" in auth.message
    paid = turn_error(perplexity.APIStatusError("pay", response=httpx.Response(402, request=req), body=None))
    assert not paid.auth and "402" in paid.message
    assert turn_error(TurnFailed("nope")).message == "nope"
    assert turn_error(TurnFailed()).message == "The request failed."
    assert turn_error(RuntimeError("bug")) is None


def test_turn_run_routes_events_and_requires_an_answer():
    from plex_cli.turn import run

    class View:
        def __init__(self):
            self.log = []

        def delta(self, text):
            self.log.append(("delta", text))

        def searching(self, queries):
            self.log.append(("searching", queries))

        def sources_found(self, sources):
            self.log.append(("sources", len(sources)))

    view = View()
    done = run(parse_stream(stream("a", "b")), view)
    assert done.text == "ab"
    assert view.log == [("searching", ["python release"]), ("sources", 2), ("delta", "a"), ("delta", "b")]
    with pytest.raises(TurnFailed, match="No answer"):
        run(iter([TextDelta("x")]), View())


# --- preset vs model mode -------------------------------------------------------------

def test_settings_mode_transitions():
    s = Settings()
    assert s.is_preset
    with pytest.raises(ValueError):
        s.set_reasoning("high")  # needs a model
    s.use_model("perplexity/sonar")
    s.set_reasoning("high")
    assert not s.is_preset and s.reasoning == "high"
    s.use_model("perplexity/sonar")  # same model keeps its level
    assert s.reasoning == "high"
    s.use_model("xai/grok-4.7")  # a new model starts from its default
    assert s.reasoning is None
    s.set_reasoning("max")
    s.use_preset("ultra")  # a preset drops the model
    assert s.is_preset and (s.effort, s.model) == ("ultra", None)
    with pytest.raises(ValueError):
        s.use_preset("bogus")
    s.use_model("")
    assert s.is_preset
