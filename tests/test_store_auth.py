import json
import stat

import pytest

from plex_cli import auth
from plex_cli.client import Source
from plex_cli.config import Settings, load_state, save_state, settings_from
from plex_cli.session import Session, Store, Turn, ago, make_title


def test_store_roundtrip_and_list(tmp_path):
    store = Store(tmp_path)
    s = Session()
    store.save(s)  # empty sessions are not written
    assert store.list() == []
    s.commit(Turn("What is   the\nlatest Python?", "3.15 [1]", {1: Source(1, "https://u", "T")}, 0.002, "m"))
    store.save(s)
    info = store.list()[0]
    assert info.id == s.id and info.title == "What is the latest Python?" and info.turns == 1
    loaded = store.load(s.id)
    assert loaded.turns[0].sources[1].url == "https://u"
    assert loaded.total_cost == pytest.approx(0.002)
    assert store.find("latest python") == s.id
    assert store.find(s.id) == s.id
    store.delete(s.id)
    assert store.list() == []


def test_store_ignores_corrupt_files_and_sanitizes_ids(tmp_path):
    (tmp_path / "bad.json").write_text("{nope")
    assert Store(tmp_path).list() == []
    assert Store(tmp_path).path("../../etc/passwd").parent == tmp_path


def test_store_skips_malformed_conversation_data(tmp_path):
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "turns.json").write_text(json.dumps({"title": 5, "turns": [
        "junk",
        {"user": "q", "assistant": "a", "cost": "x", "created_at": [1], "sources": [
            {"id": 1, "url": "https://u", "bogus": True}, {"id": 2}, {"url": "https://v"}, "junk"]},
        {"user": "no answer"},
    ]}))
    store = Store(tmp_path)
    assert [(i.id, i.title) for i in store.list()] == [("turns", "q")]
    with pytest.raises(ValueError):
        store.load("list")
    s = store.load("turns")
    assert [(t.user, t.cost) for t in s.turns] == [("q", None)]
    assert s.turns[0].sources == {1: Source(1, "https://u")}


def test_cli_open_reports_a_corrupt_conversation(tmp_path, monkeypatch, capsys):
    from plex_cli.cli import main

    (tmp_path / "bad.json").write_text("[]")
    monkeypatch.setattr("plex_cli.cli.SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("plex_cli.cli.load_state", lambda: {})
    monkeypatch.setattr("plex_cli.cli.load_file_config", lambda: {})
    assert main(["-o", "bad", "hi"]) == 2
    assert "cannot load 'bad'" in capsys.readouterr().err


def test_find_with_a_query_that_is_not_an_id(tmp_path):
    store = Store(tmp_path)
    s = Session()
    s.commit(Turn("what is 2+2?", "4"))
    store.save(s)
    assert store.find("?") == s.id  # a title search, not a crash
    assert store.find("...") is None


def test_store_reads_v1_files(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps(
        {"version": 1, "saved_at": 1, "total_cost": 0.1, "turns": [{"user": "q", "assistant": "a", "sources": []}]}))
    s = Store(tmp_path).load("old")
    assert s.id == "old" and s.title == "q" and len(s.turns) == 1


def test_title_and_ago():
    assert make_title("x" * 100).endswith("…") and len(make_title("x" * 100)) == 60
    assert ago(1000, now=1030) == "just now"
    assert ago(0, now=7200) == "2h ago"


def test_auth_file_permissions_and_logout(tmp_path):
    path = tmp_path / "plex" / "auth.json"
    auth.set_key("  pplx-secret-key-123  ", path)
    assert auth.get_key(path) == "pplx-secret-key-123"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"perplexity": {"type": "api", "key": "pplx-secret-key-123"}}
    assert auth.remove_key(path) and not path.exists()
    assert not auth.remove_key(path)
    with pytest.raises(ValueError):
        auth.set_key("   ", path)
    assert auth.mask("pplx-secret-key-123") == "pplx-…-123"


def test_logout_keeps_other_providers(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"other": {"key": "x"}}))
    auth.set_key("pplx-secret-key-123", path)
    assert auth.remove_key(path)
    assert json.loads(path.read_text()) == {"other": {"key": "x"}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["auth.json"]  # no temp file left


def test_atomic_write_keeps_old_file_on_failure(tmp_path):
    from plex_cli.files import atomic_write_json

    path = tmp_path / "a" / "state.json"
    atomic_write_json(path, {"ok": 1})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})
    assert json.loads(path.read_text()) == {"ok": 1}
    assert sorted(p.name for p in path.parent.iterdir()) == ["state.json"]  # no temp file left


def test_atomic_write_temp_file_is_per_process(tmp_path, monkeypatch):
    import os
    from plex_cli import files

    path = tmp_path / "c.json"
    seen = []
    real_replace = os.replace
    monkeypatch.setattr(files.os, "replace", lambda a, b: (seen.append(a), real_replace(a, b)))
    files.atomic_write_json(path, {})
    assert seen == [tmp_path / f"c.json.{os.getpid()}.tmp"]


def test_store_clear_removes_leftover_temp_files(tmp_path):
    import os

    store = _store_with_two(tmp_path)
    (tmp_path / f"x.json.{os.getpid()}.tmp").write_text("{")
    assert store.clear() == 2
    assert list(tmp_path.iterdir()) == []


def test_state_overrides_config(tmp_path):
    p = tmp_path / "state.json"
    save_state(Settings(effort="high", search=False, theme="nord"), path=p)
    s = settings_from({"effort": "low"}, load_state(p))
    assert (s.effort, s.search, s.theme) == ("high", False, "nord")
    assert settings_from({}, {"effort": "bogus"}).effort == "fast"  # corrupt state ignored
    # A corrupt value drops only itself.
    s = settings_from({}, {"effort": "bogus", "reasoning": "nope", "search": False, "theme": "dracula"})
    assert (s.effort, s.reasoning, s.search, s.theme) == ("fast", None, False, "dracula")


def test_save_state_writes_only_the_named_fields(tmp_path):
    p = tmp_path / "state.json"
    save_state(Settings(effort="high", search=True, theme="nord"), path=p)
    # Launched with `plex --no-search -m x`, then the theme was changed.
    s = settings_from({}, load_state(p))
    s.search = False
    s.use_model("x")
    s.theme = "dracula"
    save_state(s, ("theme",), path=p)
    state = load_state(p)
    assert (state["effort"], state["model"], state["search"], state["theme"]) == ("high", None, True, "dracula")


def _store_with_two(tmp_path):
    store = Store(tmp_path)
    for q in ("one", "two"):
        s = Session()
        s.commit(Turn(q, "a"))
        store.save(s)
    return store


@pytest.mark.parametrize("answer, kept", [("y\n", 0), ("YES\n", 0), ("n\n", 2), ("\n", 2), ("", 2)])
def test_cli_clear_confirms(tmp_path, monkeypatch, capsys, answer, kept):
    import io
    from plex_cli.cli import cmd_clear
    store = _store_with_two(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    rc = cmd_clear(store)
    assert len(store.list()) == kept
    assert rc == (0 if kept == 0 else 1)
    assert "Delete all 2 conversations?" in capsys.readouterr().out


def test_cli_clear_nothing(tmp_path, capsys):
    from plex_cli.cli import cmd_clear
    assert cmd_clear(Store(tmp_path / "missing")) == 0
    assert "No conversations" in capsys.readouterr().out


def test_conversations_are_private(tmp_path):
    store = Store(tmp_path / "plex" / "sessions")
    s = Session()
    s.commit(Turn("q", "a"))
    path = store.save(s)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.dir.parent.stat().st_mode) == 0o700


def test_cli_one_shot_reports_a_failed_save(tmp_path, monkeypatch, capsys):
    store = _store_with_two(tmp_path)
    cli = _one_shot_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "run_turn", lambda *a: True)

    def full_disk(self, s):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Store, "save", full_disk)
    assert cli.main(["-r", "hi"]) == 1
    assert "could not save the conversation" in capsys.readouterr().err
    assert len(store.list()) == 2


def _one_shot_cli(monkeypatch, tmp_path):
    import io
    from plex_cli import cli

    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(cli, "load_state", lambda: {})
    monkeypatch.setattr(cli, "load_file_config", lambda: {})
    monkeypatch.setattr(cli, "resolve_api_key", lambda cfg: "pplx-key")
    monkeypatch.setattr(cli, "Client", lambda key: None)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return cli


def test_resume_warns_when_the_newest_file_is_unreadable(tmp_path, monkeypatch, capsys):
    import os

    store = _store_with_two(tmp_path)
    bad = tmp_path / "zzz.json"
    bad.write_text("{nope")
    os.utime(bad, (2**31, 2**31))
    cli = _one_shot_cli(monkeypatch, tmp_path)
    resumed = []
    monkeypatch.setattr(cli, "run_turn", lambda client, session, *a: resumed.append(session.title) or True)
    assert cli.main(["-r", "hi"]) == 0
    assert resumed == [store.list()[0].title] and store.unreadable == [bad]
    assert "skipped zzz.json" in capsys.readouterr().err

    os.utime(bad, (1, 1))  # an old broken file doesn't matter to -r
    assert cli.main(["-r", "hi"]) == 0
    assert "skipped" not in capsys.readouterr().err
