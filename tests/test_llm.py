"""OpenAI 客户端工厂和连接检测测试。"""

from __future__ import annotations

from types import SimpleNamespace

import maoqiu.llm as llm
from maoqiu.errors import UpstreamError
from maoqiu.llm import build_client

# 不要写成 `from maoqiu.llm import test_connection`:
# pytest 会把以 test_ 开头的导入名当成测试用例收集。
check_connection = llm.test_connection


def test_build_client_rejects_incomplete_config(config):
    config.api_key = ""
    try:
        build_client(config)
    except UpstreamError as exc:
        assert "配置不完整" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应拒绝不完整配置")


def test_build_client_constructs_openai_client(config, monkeypatch):
    created = {}

    class Client:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", Client)
    client = build_client(config)
    assert isinstance(client, Client)
    assert created["api_key"] == "test-key"
    assert created["base_url"] == "https://example.invalid/v1"
    assert created["timeout"] == 120.0
    assert created["max_retries"] == 2


def test_check_connection_succeeds_with_choice(config, monkeypatch):
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())])
            )
        )
    )
    monkeypatch.setattr("maoqiu.llm.build_client", lambda _config: fake)
    ok, message = check_connection(config)
    assert ok is True
    assert config.model_name in message


def test_check_connection_rejects_empty_choices(config, monkeypatch):
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(choices=[]))
        )
    )
    monkeypatch.setattr("maoqiu.llm.build_client", lambda _config: fake)
    ok, message = check_connection(config)
    assert ok is False
    assert "空响应" in message


def test_check_connection_translates_provider_error(config, monkeypatch):
    class AuthenticationError(Exception):
        status_code = 401
        message = "bad key"

    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(AuthenticationError()))
        )
    )
    monkeypatch.setattr("maoqiu.llm.build_client", lambda _config: fake)
    ok, message = check_connection(config)
    assert ok is False
    assert "API KEY" in message


def test_check_connection_returns_config_error(config, monkeypatch):
    monkeypatch.setattr(
        "maoqiu.llm.build_client", lambda _config: (_ for _ in ()).throw(UpstreamError("缺少模型"))
    )
    ok, message = check_connection(config)
    assert ok is False
    assert message == "缺少模型"
