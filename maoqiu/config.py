"""配置加载与保存。

设计要点:
- API KEY 优先从环境变量读取, 避免明文落盘。
- 配置文件损坏时给出可读提示, 而不是抛出原始 JSON 异常。
- 所有新增字段都有默认值, 保证旧的 config.json 仍然可用。
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(os.environ.get("MAOQIU_CONFIG", "config.json"))

# 允许通过环境变量提供密钥, 按顺序查找第一个非空值。
API_KEY_ENV_VARS = ("MAOQIU_API_KEY", "OPENAI_API_KEY")
BASE_URL_ENV_VARS = ("MAOQIU_BASE_URL", "OPENAI_BASE_URL")
MODEL_ENV_VARS = ("MAOQIU_MODEL", "OPENAI_MODEL")

DEFAULT_SYSTEM_PROMPT = (
    "你是毛球, 一个运行在用户本地电脑上的 AI 助手。\n"
    "你可以使用工具查看目录、读写文件、搜索内容、执行终端命令、"
    "查询 Git 状态以及联网搜索。\n"
    "工作规则:\n"
    "1. 修改或删除文件前, 先读取相关内容确认, 不要凭猜测改动。\n"
    "2. 优先使用专用文件工具, 只有确实需要时才执行终端命令。\n"
    "3. 命令要符合当前操作系统的语法。\n"
    "4. 工具返回错误时, 说明原因并给出下一步建议, 不要重复同一个失败操作。\n"
    "5. 回答使用简体中文, 简洁直接。"
)


class ConfigError(RuntimeError):
    """配置缺失或损坏时抛出, 由调用方转换成友好提示。"""


def detect_os_name() -> str:
    system = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(system, system.lower())


def default_shell_hint(os_name: str | None = None) -> str:
    os_name = os_name or detect_os_name()
    return {
        "windows": "PowerShell (例如 Get-ChildItem、dir)",
        "macos": "zsh (例如 ls、grep)",
        "linux": "bash (例如 ls、grep)",
    }.get(os_name, "系统默认 shell")


@dataclass
class Config:
    """运行时配置。字段均可通过 config.json 覆盖。"""

    api_key: str = ""
    base_url: str = ""
    model_name: str = ""

    # 安全相关
    workspace: str = "."
    confirm_mode: str = "confirm"  # confirm | auto
    command_timeout: int = 60
    max_output_chars: int = 8000
    allow_network_tools: bool = True

    # 会话相关
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_history_messages: int = 40
    save_history: bool = True
    history_dir: str = ".maoqiu/sessions"
    log_file: str = ".maoqiu/command.log"

    os_name: str = field(default_factory=detect_os_name)

    # Web 服务相关。auth_token 为空时启动会自动生成一次性令牌。
    host: str = "127.0.0.1"
    port: int = 8767
    auth_token: str = ""

    def __post_init__(self) -> None:
        if self.confirm_mode not in ("confirm", "auto"):
            self.confirm_mode = "confirm"
        self.command_timeout = max(1, int(self.command_timeout))
        self.max_output_chars = max(500, int(self.max_output_chars))
        self.max_history_messages = max(4, int(self.max_history_messages))

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    def effective_system_prompt(self) -> str:
        """把运行环境信息拼进系统提示, 让模型生成正确语法的命令。"""
        return (
            f"{self.system_prompt}\n\n"
            f"当前操作系统: {self.os_name}; 推荐 shell: {default_shell_hint(self.os_name)}。\n"
            f"当前工作目录: {self.workspace_path}。所有文件操作都限制在该目录内。"
        )

    def to_public_dict(self) -> dict[str, Any]:
        """用于返回给前端, 不包含密钥原文。"""
        data = asdict(self)
        data.pop("api_key", None)
        data.pop("auth_token", None)
        data["api_key_set"] = bool(self.api_key)
        data["workspace"] = str(self.workspace_path)
        return data

    def to_file_dict(self) -> dict[str, Any]:
        """写入磁盘的内容。若密钥来自环境变量则不落盘。"""
        data = asdict(self)
        if _from_env(API_KEY_ENV_VARS):
            data.pop("api_key", None)
        return data


def _from_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _known_field_names() -> set[str]:
    return {f.name for f in fields(Config)}


def load_config(path: Path | None = None) -> Config:
    """读取配置。文件不存在时返回仅含环境变量的配置, 由调用方决定是否引导设置。"""
    path = path or CONFIG_FILE
    raw: dict[str, Any] = {}

    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
        try:
            parsed = json.loads(content) if content.strip() else {}
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"配置文件 {path} 不是合法的 JSON (第 {exc.lineno} 行)。"
                "请修正内容, 或删除该文件后重新配置。"
            ) from exc
        if not isinstance(parsed, dict):
            raise ConfigError(f"配置文件 {path} 顶层应为 JSON 对象。")
        allowed = _known_field_names()
        raw = {k: v for k, v in parsed.items() if k in allowed}

    for key, env_names in (
        ("api_key", API_KEY_ENV_VARS),
        ("base_url", BASE_URL_ENV_VARS),
        ("model_name", MODEL_ENV_VARS),
    ):
        env_value = _from_env(env_names)
        if env_value:
            raw[key] = env_value

    try:
        return Config(**raw)
    except TypeError as exc:
        raise ConfigError(f"配置字段类型有误: {exc}") from exc


def save_config(config: Config, path: Path | None = None) -> Path:
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_file_dict(), indent=4, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)  # 尽量限制权限, Windows 上会被忽略
    except OSError:
        pass
    return path


def is_configured(config: Config) -> bool:
    return bool(config.api_key and config.base_url and config.model_name)
