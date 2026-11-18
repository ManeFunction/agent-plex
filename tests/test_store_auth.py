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


def test_state_overrides_config(tmp_path):
    p = tmp_path / "state.json"
    save_state(Settings(effort="high", search=False, theme="nord"), p)
    s = settings_from({"effort": "low"}, load_state(p))
    assert (s.effort, s.search, s.theme) == ("high", False, "nord")
    assert settings_from({}, {"effort": "bogus"}).effort == "fast"  # corrupt state ignored


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
