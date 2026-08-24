from __future__ import annotations

import json

import pytest

from maoqiu.config import Config, ConfigError, load_config, save_config


def test_env_overrides_config_file(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_key": "file-key", "base_url": "https://file", "model_name": "file-model"}), encoding="utf-8")
    monkeypatch.setenv("MAOQIU_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env")
    config = load_config(path)
    assert config.api_key == "env-key"
    assert config.base_url == "https://env"
    assert config.model_name == "file-model"


def test_bad_config_gives_friendly_error(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"api_key": ', encoding="utf-8")
    with pytest.raises(ConfigError, match="不是合法的 JSON"):
        load_config(path)


def test_unknown_fields_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model_name": "test", "made_up": "ignored"}), encoding="utf-8")
    config = load_config(path)
    assert config.model_name == "test"
    assert not hasattr(config, "made_up")


def test_values_clamped():
    config = Config(command_timeout=0, max_output_chars=2, max_history_messages=1, confirm_mode="unsafe")
    assert config.command_timeout == 1
    assert config.max_output_chars == 500
    assert config.max_history_messages == 4
    assert config.confirm_mode == "confirm"


def test_public_config_never_exposes_key():
    config = Config(api_key="super-secret-key")
    result = config.to_public_dict()
    assert "api_key" not in result
    assert result["api_key_set"] is True


def test_env_key_not_written(monkeypatch, tmp_path):
    monkeypatch.setenv("MAOQIU_API_KEY", "environment-secret")
    config = Config(api_key="environment-secret")
    path = save_config(config, tmp_path / "config.json")
    raw = path.read_text(encoding="utf-8")
    assert "environment-secret" not in raw
    assert "api_key" not in json.loads(raw)


def test_roundtrip(tmp_path):
    original = Config(api_key="file-key", base_url="https://api.example/v1", model_name="demo", workspace=str(tmp_path / "space"), port=9999)
    path = save_config(original, tmp_path / "config.json")
    loaded = load_config(path)
    assert loaded.api_key == "file-key"
    assert loaded.base_url == "https://api.example/v1"
    assert loaded.model_name == "demo"
    assert loaded.port == 9999
