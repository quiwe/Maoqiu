"""工具契约与注册表。

每个工具用 @register 声明, 自动生成 OpenAI function calling 所需的 schema,
避免像旧版那样手写 tools_menu 后与实现脱节。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..errors import PolicyDenied, SandboxViolation, ToolExecutionError
from ..security import Decision


@dataclass
class ToolContext:
    """工具执行时可用的运行环境。"""

    config: Config
    # 确认回调: 返回 True 表示用户同意执行。None 表示无人可问(直接拒绝需确认的操作)。
    confirm: Callable[[str, str, dict[str, Any]], bool] | None = None
    log: Callable[[str], None] | None = None

    def write_log(self, message: str) -> None:
        if self.log:
            self.log(message)


@dataclass
class ToolResult:
    """统一的返回结构, 明确区分成功与失败。"""

    ok: bool
    data: str = ""
    error: str = ""
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_model_content(self) -> str:
        """转成给模型看的字符串。失败时明确写出 ERROR, 避免模型误判成功。"""
        if self.ok:
            body = self.data or "执行成功, 无输出内容。"
            if self.truncated:
                body += "\n[注意: 输出已被截断]"
            return body
        return f"ERROR: {self.error}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "truncated": self.truncated,
            "meta": self.meta,
        }


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]
    # 静态风险等级: read 类工具无需确认, write/exec 类交由策略或此处判定
    risk: str = "read"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def register(
    name: str,
    description: str,
    parameters: dict[str, Any],
    risk: str = "read",
) -> Callable[[Callable[[ToolContext, dict[str, Any]], ToolResult]], Callable[..., ToolResult]]:
    def decorator(
        func: Callable[[ToolContext, dict[str, Any]], ToolResult],
    ) -> Callable[[ToolContext, dict[str, Any]], ToolResult]:
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            risk=risk,
        )
        return func

    return decorator


def tool_schemas(config: Config) -> list[dict[str, Any]]:
    """返回当前配置下可用的工具列表。"""
    schemas = []
    for tool in REGISTRY.values():
        if tool.risk == "network" and not config.allow_network_tools:
            continue
        schemas.append(tool.schema())
    return schemas


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """解析模型给出的参数。非法 JSON 转成可读错误而不是崩溃。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolExecutionError(f"工具参数不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolExecutionError("工具参数应为 JSON 对象。")
    return parsed


def require_str(args: dict[str, Any], key: str, default: str | None = None) -> str:
    value = args.get(key, default)
    if value is None:
        raise ToolExecutionError(f"缺少必填参数 {key}。")
    if not isinstance(value, str):
        raise ToolExecutionError(f"参数 {key} 应为字符串, 实际为 {type(value).__name__}。")
    return value


def optional_int(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(f"参数 {key} 应为整数。") from exc


def ask_confirmation(ctx: ToolContext, summary: str, detail: str, payload: dict[str, Any]) -> None:
    """在 confirm 模式下征求用户同意; auto 模式直接通过。"""
    if ctx.config.confirm_mode == "auto":
        ctx.write_log(f"[auto] {summary}")
        return
    if ctx.confirm is None:
        raise PolicyDenied(f"{summary} 需要确认, 但当前没有可用的确认通道, 已拒绝。")
    if not ctx.confirm(summary, detail, payload):
        raise PolicyDenied(f"用户拒绝了操作: {summary}")


def run_tool(name: str, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """执行工具并把异常收敛为 ToolResult。"""
    tool = REGISTRY.get(name)
    if tool is None:
        available = ", ".join(sorted(REGISTRY))
        return ToolResult(ok=False, error=f"未知工具 {name}。可用工具: {available}")
    if tool.risk == "network" and not ctx.config.allow_network_tools:
        return ToolResult(ok=False, error="网络工具已在配置中禁用。")
    try:
        return tool.handler(ctx, args)
    except (PolicyDenied, SandboxViolation) as exc:
        return ToolResult(ok=False, error=str(exc), meta={"kind": "policy"})
    except ToolExecutionError as exc:
        return ToolResult(ok=False, error=str(exc), meta={"kind": "tool"})
    except Exception as exc:  # noqa: BLE001 - 兜底, 防止单个工具异常中断整个会话
        return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}", meta={"kind": "unexpected"})
