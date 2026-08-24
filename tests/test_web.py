"""Web 层测试: 鉴权、会话、SSE 对话与确认回路。

全部离线运行: 通过 monkeypatch 注入假 OpenAI 客户端。
"""

from __future__ import annotations

import pytest
from conftest import FakeClient, make_message, make_tool_call

from maoqiu.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def web_client(config):
    from fastapi.testclient import TestClient

    config.auth_token = TOKEN
    config.port = 8767
    return TestClient(create_app(config))


def auth(origin: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if origin:
        headers["Origin"] = origin
    return headers


def new_session_id(web_client) -> str:
    return web_client.post("/api/sessions", headers=auth()).json()["id"]


def sse_events(text: str) -> list[str]:
    return [line.removeprefix("event: ").strip() for line in text.splitlines() if line.startswith("event: ")]


# ----------------------------------------------------------------------
# 鉴权
# ----------------------------------------------------------------------


def test_health_is_open_but_never_leaks_token(web_client):
    """/api/health 匿名可读, 因此绝不能返回令牌。"""
    response = web_client.get("/api/health")
    body = response.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["auth_required"] is True
    assert "access_token" not in body
    assert TOKEN not in response.text


def test_config_api_cannot_change_token_or_bind_address(web_client):
    """防止通过配置接口固定令牌或把下次启动改成监听 0.0.0.0。"""
    before = web_client.app.state.auth_token
    response = web_client.put(
        "/api/config",
        headers=auth(),
        json={"auth_token": "attacker-token", "host": "0.0.0.0", "port": 9999},
    )
    assert response.status_code == 200
    config = web_client.app.state.config
    assert web_client.app.state.auth_token == before
    assert config.auth_token != "attacker-token"
    assert config.host != "0.0.0.0"
    assert web_client.get("/api/config", headers=auth()).status_code == 200


def test_api_requires_valid_token(web_client):
    assert web_client.get("/api/config").status_code == 401
    assert web_client.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert web_client.get("/api/config", headers=auth()).status_code == 200


def test_cross_site_origin_rejected(web_client):
    """阻止任意网页借浏览器调用本地工具接口。"""
    response = web_client.get("/api/config", headers=auth(origin="http://evil.example"))
    assert response.status_code == 403


def test_public_config_hides_secrets(web_client):
    body = web_client.get("/api/config", headers=auth()).json()
    assert "api_key" not in body
    assert "auth_token" not in body
    assert body["api_key_set"] is True


# ----------------------------------------------------------------------
# 会话
# ----------------------------------------------------------------------


def test_session_crud(web_client):
    created = web_client.post("/api/sessions", headers=auth()).json()
    session_id = created["id"]
    assert created["messages"][0]["role"] == "system"

    assert any(item["id"] == session_id for item in web_client.get("/api/sessions", headers=auth()).json())

    renamed = web_client.patch(f"/api/sessions/{session_id}", headers=auth(), json={"title": "测试会话"})
    assert renamed.json()["title"] == "测试会话"

    assert web_client.delete(f"/api/sessions/{session_id}", headers=auth()).status_code == 200
    assert web_client.get(f"/api/sessions/{session_id}", headers=auth()).status_code == 404


def test_unknown_session_returns_404(web_client):
    assert web_client.get("/api/sessions/does-not-exist", headers=auth()).status_code == 404


def test_export_markdown(web_client):
    session_id = new_session_id(web_client)
    response = web_client.get(f"/api/sessions/{session_id}/export", headers=auth())
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]


def test_empty_api_key_keeps_existing(web_client):
    response = web_client.put("/api/config", headers=auth(), json={"api_key": "", "confirm_mode": "auto"})
    assert response.status_code == 200
    assert response.json()["api_key_set"] is True
    assert response.json()["confirm_mode"] == "auto"


# ----------------------------------------------------------------------
# SSE 对话
# ----------------------------------------------------------------------


def test_chat_stream_plain_reply(web_client, monkeypatch):
    monkeypatch.setattr(
        "maoqiu.web.app.build_client",
        lambda _config: FakeClient([make_message("你好, 我是毛球。")]),
    )
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "你好"}
    )
    assert response.status_code == 200
    assert sse_events(response.text) == ["text", "done"]
    assert "毛球" in response.text


def test_chat_stream_runs_tool_then_replies(web_client, monkeypatch):
    monkeypatch.setattr(
        "maoqiu.web.app.build_client",
        lambda _config: FakeClient(
            [
                make_message("", [make_tool_call("c1", "list_dir", "{}")]),
                make_message("目录里有 hello.txt。"),
            ]
        ),
    )
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "看看目录"}
    )
    assert sse_events(response.text) == ["tool_call", "tool_result", "text", "done"]
    assert "hello.txt" in response.text


def test_write_requires_confirm_flag(web_client, monkeypatch):
    """confirm=false 时写文件必须被拒绝, 文件不能落盘。"""
    script = [
        make_message("", [make_tool_call("c1", "write_file", '{"path": "web_new.txt", "content": "x"}')]),
        make_message("需要你确认后才能写入。"),
    ]
    monkeypatch.setattr("maoqiu.web.app.build_client", lambda _config: FakeClient(script))
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "写个文件", "confirm": False}
    )
    assert "拒绝" in response.text
    assert not (web_client.app.state.config.workspace_path / "web_new.txt").exists()


def test_write_succeeds_with_confirm_flag(web_client, monkeypatch):
    script = [
        make_message("", [make_tool_call("c1", "write_file", '{"path": "web_ok.txt", "content": "内容"}')]),
        make_message("已经写好了。"),
    ]
    monkeypatch.setattr("maoqiu.web.app.build_client", lambda _config: FakeClient(script))
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "写个文件", "confirm": True}
    )
    assert response.status_code == 200
    target = web_client.app.state.config.workspace_path / "web_ok.txt"
    assert target.read_text(encoding="utf-8") == "内容"


def test_confirm_retry_reexecutes_rejected_tool(web_client, monkeypatch):
    """先拒绝写入, 再 confirm 重试时必须重新调工具而不是只返回旧结论。"""
    clients = [
        FakeClient(
            [
                make_message("", [make_tool_call("c1", "write_file", '{"path": "retry.txt", "content": "重试成功"}')]),
                make_message("等待确认。"),
            ]
        ),
        FakeClient(
            [
                make_message("", [make_tool_call("c2", "write_file", '{"path": "retry.txt", "content": "重试成功"}')]),
                make_message("已经写好了。"),
            ]
        ),
    ]
    monkeypatch.setattr("maoqiu.web.app.build_client", lambda _config: clients.pop(0))
    session_id = new_session_id(web_client)

    first = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "写文件", "confirm": False}
    )
    assert "用户拒绝" in first.text
    target = web_client.app.state.config.workspace_path / "retry.txt"
    assert not target.exists()

    second = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "写文件", "confirm": True}
    )
    assert sse_events(second.text) == ["tool_call", "tool_result", "text", "done"]
    assert target.read_text(encoding="utf-8") == "重试成功"


def test_dangerous_command_blocked_even_with_confirm(web_client, monkeypatch):
    """deny 级命令即使用户点了确认也不能执行。"""
    script = [
        make_message("", [make_tool_call("c1", "run_command", '{"cmd": "rm -rf /"}')]),
        make_message("这条命令我不会执行。"),
    ]
    monkeypatch.setattr("maoqiu.web.app.build_client", lambda _config: FakeClient(script))
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "清空磁盘", "confirm": True}
    )
    assert "安全策略拒绝" in response.text


def test_upstream_error_becomes_sse_error(web_client, monkeypatch):
    def fail(_config):
        from maoqiu.errors import UpstreamError

        raise UpstreamError("配置不完整")

    monkeypatch.setattr("maoqiu.web.app.build_client", fail)
    session_id = new_session_id(web_client)
    response = web_client.post(
        f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "你好"}
    )
    assert response.status_code == 200
    assert sse_events(response.text) == ["error"]
    assert "配置不完整" in response.text


def test_conversation_is_persisted(web_client, monkeypatch):
    monkeypatch.setattr(
        "maoqiu.web.app.build_client", lambda _config: FakeClient([make_message("记住了。")])
    )
    session_id = new_session_id(web_client)
    web_client.post(f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "第一条消息"})

    session = web_client.get(f"/api/sessions/{session_id}", headers=auth()).json()
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert session["title"] == "第一条消息"


def test_empty_message_rejected(web_client):
    session_id = new_session_id(web_client)
    response = web_client.post(f"/api/sessions/{session_id}/messages", headers=auth(), json={"content": "   "})
    assert response.status_code == 400


# ----------------------------------------------------------------------
# 静态资源与首次配置
# ----------------------------------------------------------------------


def test_index_and_static_assets(web_client):
    assert "毛球" in web_client.get("/").text
    assert web_client.get("/static/app.js").status_code == 200
    assert web_client.get("/static/style.css").status_code == 200


def test_static_path_traversal_blocked(web_client):
    assert web_client.get("/static/../../config.json").status_code in (403, 404)


def test_setup_state_when_not_configured(config):
    from fastapi.testclient import TestClient

    config.api_key = ""
    config.model_name = ""
    config.auth_token = TOKEN
    client = TestClient(create_app(config))
    assert client.get("/api/health").json()["configured"] is False
