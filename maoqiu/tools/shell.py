"""终端命令工具(阶段 1 与 2 的核心)。

与旧版的区别:
- 执行前先经过 classify_command 三态判定
- 危险命令直接拒绝, 有副作用的命令需要用户确认
- 有超时、退出码、输出截断
- stdout 与 stderr 分开呈现, 不再用 `stdout or stderr` 混淆成败
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..errors import PolicyDenied
from ..security import classify_command, redact, truncate
from .base import ToolContext, ToolResult, ask_confirmation, optional_int, register, require_str

SHELL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cmd": {
            "type": "string",
            "description": "要执行的完整命令行, 需符合当前操作系统的 shell 语法",
        },
        "purpose": {
            "type": "string",
            "description": "用一句话说明执行这条命令的目的, 会展示给用户确认",
        },
        "timeout": {
            "type": "integer",
            "description": "超时秒数, 省略则使用配置默认值",
        },
    },
    "required": ["cmd"],
}


@register(
    name="run_command",
    description=(
        "在终端执行一条命令并返回输出。适合运行测试、查看进程、调用项目脚本等。"
        "查看目录或读写文件请优先使用专用文件工具。"
        "危险命令会被安全策略拒绝, 有副作用的命令需要用户确认。"
    ),
    parameters=SHELL_PARAMETERS,
    risk="exec",
)
def run_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    cmd = require_str(args, "cmd").strip()
    purpose = args.get("purpose") or "未说明用途"
    timeout = optional_int(args, "timeout", ctx.config.command_timeout)
    timeout = max(1, min(timeout, 600))

    decision = classify_command(cmd)
    ctx.write_log(f"[policy:{decision.action}] {cmd} <- {decision.reason}")

    if decision.denied:
        raise PolicyDenied(
            f"命令被安全策略拒绝({decision.reason}): {cmd}\n"
            "如确实需要执行, 请手动在终端运行并自行确认后果。"
        )

    if decision.needs_confirmation:
        ask_confirmation(
            ctx,
            summary=f"执行命令: {cmd}",
            detail=f"原因: {decision.reason}\n用途: {purpose}",
            payload={"cmd": cmd, "reason": decision.reason, "kind": "command"},
        )

    workspace = ctx.config.workspace_path
    try:
        completed = subprocess.run(  # noqa: S602 - 需要 shell 语法, 已由策略层前置校验
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(workspace) if workspace.exists() else None,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            error=f"命令超过 {timeout} 秒未结束, 已终止: {cmd}",
            meta={"cmd": cmd, "timeout": timeout},
        )
    except OSError as exc:
        return ToolResult(ok=False, error=f"无法启动命令: {exc}", meta={"cmd": cmd})

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    combined = "\n\n".join(parts) or "(无输出)"
    combined, was_truncated = truncate(redact(combined), ctx.config.max_output_chars)

    ok = completed.returncode == 0
    body = f"退出码: {completed.returncode}\n\n{combined}"
    if ok:
        return ToolResult(
            ok=True,
            data=body,
            truncated=was_truncated,
            meta={"cmd": cmd, "returncode": completed.returncode},
        )
    return ToolResult(
        ok=False,
        error=f"命令以退出码 {completed.returncode} 结束。\n\n{combined}",
        truncated=was_truncated,
        meta={"cmd": cmd, "returncode": completed.returncode},
    )
