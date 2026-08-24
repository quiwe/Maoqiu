"""命令行入口。

默认进入终端对话模式; 加 --web 启动本地 Web 界面。
终端模式与 Web 共用 maoqiu.core.Agent, 只是把事件渲染成文字。
"""

from __future__ import annotations

import argparse
import secrets
import sys
import webbrowser
from pathlib import Path

from .config import (
    Config,
    ConfigError,
    default_shell_hint,
    is_configured,
    load_config,
    save_config,
)
from .core import Agent
from .errors import UpstreamError
from .llm import build_client
from .session import SessionStore

BANNER = r"""
  毛球 AI 助手  v2.0
  安全边界: 工作目录限制 + 命令三态策略 + 危险操作确认
"""

HELP_TEXT = """
可用指令:
  /help              查看本帮助
  /clear             清空当前对话上下文
  /history           查看当前会话消息
  /save              手动保存当前会话
  /load <会话ID>     加载历史会话
  /sessions          列出全部会话
  /config            查看当前配置
  /mode auto|confirm 切换确认模式
  /tools             列出可用工具
  /web               启动 Web 界面
  exit / quit / q    退出
"""


def guide_setup(config: Config) -> Config:
    """首次运行的交互式配置。密钥不回显在提示里。"""
    print("\n" + "=" * 46)
    print("检测到尚未完成配置, 请填写模型服务信息。")
    print("提示: 也可以用环境变量 MAOQIU_API_KEY / OPENAI_API_KEY 提供密钥。")
    print("=" * 46)

    def ask(prompt: str, current: str) -> str:
        while True:
            value = input(prompt).strip()
            if value:
                return value
            if current:
                return current
            print("这一项是必填的。")

    config.api_key = ask("\n1. API KEY: ", config.api_key)
    config.base_url = ask("2. API 基础地址 (如 https://api.openai.com/v1): ", config.base_url)
    config.model_name = ask("3. 模型名称 (如 gpt-4o-mini): ", config.model_name)

    path = save_config(config)
    print(f"\n配置已保存到 {path}(权限 600)。下次启动自动加载。")
    return config


def confirm_in_terminal(summary: str, detail: str, payload: dict) -> bool:
    print(f"\n  需要确认: {summary}")
    if detail:
        for line in detail.splitlines():
            print(f"    {line}")
    try:
        answer = input("  是否执行? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes", "是")


def render_events(agent: Agent, messages: list[dict], user_input: str) -> None:
    for event in agent.run_turn(messages, user_input):
        if event.type == "text":
            print(f"\n毛球 > {event.data['content']}")
        elif event.type == "tool_call":
            args = event.data.get("arguments", {})
            preview = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items())
            print(f"\n  [工具] {event.data['name']}({preview})")
        elif event.type == "tool_result":
            if event.data.get("ok"):
                data = (event.data.get("data") or "").strip()
                first_line = data.splitlines()[0] if data else "完成"
                print(f"  [完成] {first_line[:120]}")
            else:
                print(f"  [失败] {event.data.get('error', '')[:300]}")
        elif event.type == "error":
            print(f"\n出错了: {event.data['message']}")


def run_terminal(config: Config) -> int:
    print(BANNER)
    if not is_configured(config):
        config = guide_setup(config)

    try:
        client = build_client(config)
    except UpstreamError as exc:
        print(f"无法初始化模型客户端: {exc}")
        return 1

    store = SessionStore(config)
    session = store.create()
    agent = Agent(config, client, confirm=confirm_in_terminal)

    print(f"工作目录: {config.workspace_path}")
    print(f"系统: {config.os_name} | shell: {default_shell_hint(config.os_name)}")
    print(f"模型: {config.model_name} | 确认模式: {config.confirm_mode}")
    print("输入 /help 查看指令, exit 退出。")

    while True:
        try:
            user_input = input("\n我 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n毛球已退出。")
            return 0

        if not user_input:
            continue

        lowered = user_input.lower()
        if lowered in ("exit", "quit", "q", "退出"):
            store.save(session)
            print("毛球已退出。")
            return 0

        if lowered.startswith("/"):
            parts = user_input.split(maxsplit=1)
            command = parts[0].lower()
            argument = parts[1].strip() if len(parts) > 1 else ""

            if command == "/help":
                print(HELP_TEXT)
            elif command == "/clear":
                store.clear_messages(session)
                print("上下文已清空。")
            elif command == "/history":
                for message in session.visible_messages():
                    role = {"user": "我", "assistant": "毛球", "tool": "工具"}.get(
                        str(message.get("role")), str(message.get("role"))
                    )
                    content = str(message.get("content") or "")[:160]
                    print(f"  [{role}] {content}")
            elif command == "/save":
                store.save(session)
                print(f"已保存会话 {session.id}。")
            elif command == "/sessions":
                for item in store.list():
                    print(f"  {item['id']}  {item['title'][:34]:<36} {item['message_count']} 条")
            elif command == "/load":
                loaded = store.get(argument)
                if loaded:
                    session = loaded
                    agent = Agent(config, client, confirm=confirm_in_terminal)
                    print(f"已加载会话 {session.id}: {session.title}")
                else:
                    print("找不到该会话 ID。")
            elif command == "/config":
                for key, value in config.to_public_dict().items():
                    if key == "system_prompt":
                        value = str(value)[:60] + "..."
                    print(f"  {key}: {value}")
            elif command == "/mode":
                if argument in ("auto", "confirm"):
                    config.confirm_mode = argument
                    save_config(config)
                    print(f"确认模式已切换为 {argument}。")
                else:
                    print("用法: /mode auto 或 /mode confirm")
            elif command == "/tools":
                from .tools import REGISTRY

                for name, tool in sorted(REGISTRY.items()):
                    print(f"  {name:<18} [{tool.risk}] {tool.description[:60]}")
            elif command == "/web":
                return run_web(config)
            else:
                print("未知指令, 输入 /help 查看可用指令。")
            continue

        try:
            render_events(agent, session.messages, user_input)
        except KeyboardInterrupt:
            print("\n已中断本轮操作。")
        store.save(session)


def run_web(config: Config, open_browser: bool = True) -> int:
    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn, 请运行: pip install -r requirements.txt")
        return 1

    from .web.app import create_app

    if not config.auth_token:
        config.auth_token = secrets.token_urlsafe(24)

    app = create_app(config)
    url = f"http://{config.host}:{config.port}/?token={config.auth_token}"

    print(BANNER)
    print(f"Web 界面已启动: {url}")
    print("此令牌仅本次启动有效, 请勿分享该链接。")
    if config.host not in ("127.0.0.1", "localhost"):
        print("警告: 监听地址不是 127.0.0.1。暴露到局域网等于把本机 shell 交出去。")
    print("按 Ctrl+C 停止。\n")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
            pass

    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maoqiu", description="毛球 AI 助手")
    parser.add_argument("--web", action="store_true", help="启动本地 Web 界面")
    parser.add_argument("--host", default=None, help="Web 监听地址, 默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="Web 端口, 默认 8767")
    parser.add_argument("--workspace", default=None, help="限定 Agent 可操作的工作目录")
    parser.add_argument("--auto", action="store_true", help="自动执行副作用操作(谨慎使用)")
    parser.add_argument("--no-browser", action="store_true", help="Web 模式下不自动打开浏览器")
    parser.add_argument("--config", default=None, help="指定配置文件路径")
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"配置有问题: {exc}")
        return 1

    if args.workspace:
        candidate = Path(args.workspace).expanduser()
        if not candidate.is_dir():
            print(f"工作目录不存在: {candidate}")
            return 1
        config.workspace = str(candidate.resolve())
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.auto:
        config.confirm_mode = "auto"

    if args.web:
        return run_web(config, open_browser=not args.no_browser)
    return run_terminal(config)


if __name__ == "__main__":
    sys.exit(main())
