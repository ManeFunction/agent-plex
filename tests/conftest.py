import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never touch the real ~/.local/share/plex/state.json."""
    monkeypatch.setattr("plex_cli.config.STATE_FILE", tmp_path / "state.json")
