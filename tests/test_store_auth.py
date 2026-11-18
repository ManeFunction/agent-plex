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
