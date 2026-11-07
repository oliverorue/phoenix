"""Tests de estado compartido entre bot y dashboard."""

import json

import bot.dashboard.shared_state as shared_state


def test_get_state_reloads_external_update(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(shared_state, "STATE_FILE", state_file)

    observer = shared_state.BotState()
    assert observer.get_state()["balance"] == 0.0

    state_file.write_text(json.dumps({"balance": 123.45, "bot_running": True}))

    state = observer.get_state()
    assert state["balance"] == 123.45
    assert state["bot_running"] is True


def test_get_state_keeps_local_state_when_file_is_missing(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(shared_state, "STATE_FILE", state_file)

    observer = shared_state.BotState()
    observer.update({"balance": 50.0})

    assert observer.get_state()["balance"] == 50.0
