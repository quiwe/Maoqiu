"""FastAPI Web 层。

默认只监听 127.0.0.1。若配置了 auth_token, 所有 /api 请求必须带
Authorization: Bearer <token>。前端是无构建步骤的静态 vanilla JS。
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Config, ConfigError, is_configured, load_config, save_config
from ..core import Agent, Event
from ..errors import UpstreamError
from ..llm import build_client, test_connection
from ..session import Session, SessionStore

STATIC_DIR = Path(__file__).parent / "static"


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    confirm: bool = False


class ConfigBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    workspace: str | None = None
    confirm_mode: str | None = None
    command_timeout: int | None = None
    max_output_chars: int | None = None
    allow_network_tools: bool | None = None
    system_prompt: str | None = None
    max_history_messages: int | None = None
    save_history: bool | None = None
    history_dir: str | None = None
    log_file: str | None = None
    # 故意不暴露 auth_token / host / port:
    # 前者会让攻击者把令牌固定成已知值, 后者可以把下次启动改成监听 0.0.0.0。
    # 这些只能通过命令行参数或配置文件由本人设置。


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ConnectionResult(BaseModel):
    ok: bool
    message: str


def _sse(event: Event) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    # auth_token 未配置时生成令牌并打印到启动日志, 但不写回配置文件。
    runtime_token = config.auth_token or secrets.token_urlsafe(24)
    store = SessionStore(config)

    app = FastAPI(title="毛球 AI 助手", version="2.0.0", docs_url="/api/docs")
    app.state.config = config
    app.state.store = store
    app.state.auth_token = runtime_token

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path.startswith("/api") and request.url.path != "/api/health":
            auth = request.headers.get("authorization", "")
            if not secrets.compare_digest(auth.removeprefix("Bearer ").strip(), runtime_token):
                return JSONResponse({"detail": "需要有效的 Bearer token。"}, status_code=401)
        # 不允许浏览器从任意站点调用本地工具接口。
        origin = request.headers.get("origin")
        if origin and origin not in (f"http://{config.host}:{config.port}", "null"):
            return JSONResponse({"detail": "Origin 不被允许。"}, status_code=403)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/static/{filename:path}", include_in_schema=False)
    async def static_file(filename: str):
        path = (STATIC_DIR / filename).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "静态文件不存在")
        return FileResponse(path)

    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "version": "2.0.0",
            "configured": is_configured(config),
            "model": config.model_name,
            "workspace": str(config.workspace_path),
            "os": config.os_name,
            "auth_required": True,
            # 这里绝对不能返回 runtime_token: /api/health 是匿名接口,
            # 泄露令牌等于把命令执行权限交给本机任意进程。
            # 令牌只通过启动时打印的 URL 传给浏览器。
        }

    @app.get("/api/config")
    async def get_config():
        return config.to_public_dict()

    @app.put("/api/config")
    async def put_config(body: ConfigBody):
        values = body.model_dump(exclude_unset=True)
        # API KEY 空字符串表示保留现有值, 不允许通过公共接口读取密钥。
        if not values.get("api_key"):
            values.pop("api_key", None)
        for key, value in values.items():
            if hasattr(config, key):
                setattr(config, key, value)
        config.__post_init__()
        save_config(config)
        return config.to_public_dict()

    @app.post("/api/config/test", response_model=ConnectionResult)
    async def config_test(body: ConfigBody):
        values = body.model_dump(exclude_unset=True)
        candidate = Config(**{**config.to_file_dict(), **values})
        ok, message = test_connection(candidate)
        return ConnectionResult(ok=ok, message=message)

    def get_session(session_id: str) -> Session:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        return session

    @app.get("/api/sessions")
    async def list_sessions():
        return store.list()

    @app.post("/api/sessions")
    async def create_session():
        return store.create().to_dict()

    @app.get("/api/sessions/{session_id}")
    async def get_session_detail(session_id: str):
        session = get_session(session_id)
        return session.to_dict()

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: RenameBody):
        session = get_session(session_id)
        session.title = body.title.strip()
        store.save(session)
        return session.to_dict()

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        if not store.delete(session_id):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/clear")
    async def clear_session(session_id: str):
        return store.clear_messages(get_session(session_id)).to_dict()

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str, format: str = "markdown"):
        session = get_session(session_id)
        if format == "json":
            return JSONResponse(session.to_dict(), headers={"Content-Disposition": f'attachment; filename="{session.id}.json"'})
        from fastapi.responses import Response

        return Response(
            store.export_markdown(session),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{session.id}.md"'},
        )

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, body: MessageBody):
        session = get_session(session_id)
        text = body.content.strip()
        if not text:
            raise HTTPException(400, "消息不能为空")

        # 用户点"确认后重试"时, 先回滚上一轮被确认门拦下的工具轮次,
        # 否则模型会以为该操作已处理过, 只给结论而不再真正执行工具。
        if body.confirm:
            store.rollback_unconfirmed_turn(session)

        try:
            client = build_client(config)
        except UpstreamError as exc:
            # 先把消息取出成普通字符串: except 结束后 exc 会被解绑,
            # 异步生成器是延迟执行的, 直接闭包捕获 exc 会抛 NameError。
            message = str(exc)

            async def config_error_stream():
                yield _sse(Event("error", {"message": message}))

            return StreamingResponse(config_error_stream(), media_type="text/event-stream")

        # 确认模式下, 只有本次请求显式 confirm=true 才允许有副作用的工具。
        def confirm(summary: str, detail: str, payload: dict[str, Any]) -> bool:
            return body.confirm

        agent = Agent(config, client, confirm=confirm)
        session.title = session.derive_title() if session.title == "新对话" else session.title

        async def stream():
            try:
                for event in agent.run_turn(session.messages, text):
                    yield _sse(event)
                session.title = session.derive_title()
                store.save(session)
            except Exception as exc:  # noqa: BLE001
                yield _sse(Event("error", {"message": f"处理失败: {type(exc).__name__}: {exc}"}))

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def build_default_app() -> FastAPI:
    """给 `uvicorn maoqiu.web.app:build_default_app --factory` 之类的用法留的入口。

    注意: 不在模块导入时就构造应用。导入即建应用会在测试和工具链里
    产生意外副作用(读配置、建会话目录、生成令牌)。
    """
    try:
        config = load_config()
    except ConfigError:
        config = Config()
    return create_app(config)
