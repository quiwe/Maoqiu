"""Git 与项目辅助工具(阶段 4)。

只读的 Git 查询直接放行, 写操作(commit)需要确认。
命令通过 argv 列表执行, 不经过 shell, 避免注入。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..errors import ToolExecutionError
from ..security import redact, truncate
from .base import (
    ToolContext,
    ToolResult,
    ask_confirmation,
    optional_int,
    register,
    require_str,
)


def _run_git(ctx: ToolContext, argv: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    workspace = ctx.config.workspace_path
    if not (workspace / ".git").exists():
        raise ToolExecutionError(f"{workspace} 不是 Git 仓库。")
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or ctx.config.command_timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolExecutionError("系统中找不到 git 命令。") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolExecutionError("git 命令超时。") from exc
    return completed.returncode, (completed.stdout or "").strip(), (completed.stderr or "").strip()


def _result(ctx: ToolContext, code: int, out: str, err: str, empty_hint: str) -> ToolResult:
    if code != 0:
        return ToolResult(ok=False, error=f"git 退出码 {code}: {err or out}")
    body, was_truncated = truncate(redact(out or empty_hint), ctx.config.max_output_chars)
    return ToolResult(ok=True, data=body, truncated=was_truncated)


@register(
    name="git_status",
    description="查看当前 Git 仓库的分支、改动文件和提交状态。",
    parameters={"type": "object", "properties": {}},
)
def git_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    code, out, err = _run_git(ctx, ["status", "--short", "--branch"])
    return _result(ctx, code, out, err, "工作区干净, 没有未提交的改动。")


@register(
    name="git_diff",
    description="查看未提交的代码改动。可指定文件路径, 或用 staged=true 查看已暂存的改动。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "限定某个文件或目录"},
            "staged": {"type": "boolean", "description": "是否查看已暂存的改动, 默认 false"},
        },
    },
)
def git_diff(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    argv = ["diff"]
    if args.get("staged"):
        argv.append("--staged")
    path = args.get("path")
    if path:
        if not isinstance(path, str):
            raise ToolExecutionError("path 应为字符串。")
        if path.startswith("-"):
            raise ToolExecutionError("path 不能以 - 开头。")
        argv.extend(["--", path])
    code, out, err = _run_git(ctx, argv)
    return _result(ctx, code, out, err, "没有差异。")


@register(
    name="git_log",
    description="查看最近的提交历史。",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "显示多少条, 默认 10"}},
    },
)
def git_log(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    limit = max(1, min(optional_int(args, "limit", 10), 100))
    code, out, err = _run_git(ctx, ["log", f"-{limit}", "--pretty=format:%h %ad %an: %s", "--date=short"])
    return _result(ctx, code, out, err, "还没有提交记录。")


@register(
    name="git_commit",
    description="把指定文件加入暂存区并创建一次提交。需要用户确认。不会推送到远端。",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "提交信息"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提交的文件路径列表。建议显式指定, 避免误提交无关改动。",
            },
        },
        "required": ["message", "paths"],
    },
    risk="write",
)
def git_commit(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    message = require_str(args, "message").strip()
    paths = args.get("paths")
    if not message:
        raise ToolExecutionError("提交信息不能为空。")
    if not isinstance(paths, list) or not paths:
        raise ToolExecutionError("paths 必须是非空的文件路径列表。")
    safe_paths: list[str] = []
    for item in paths:
        if not isinstance(item, str) or not item.strip():
            raise ToolExecutionError("paths 中的每一项都应为非空字符串。")
        if item.startswith("-"):
            raise ToolExecutionError("路径不能以 - 开头。")
        safe_paths.append(item.strip())

    workspace = ctx.config.workspace_path
    for item in safe_paths:
        candidate = (workspace / item).resolve()
        if candidate != workspace and workspace not in candidate.parents:
            raise ToolExecutionError(f"{item} 超出工作目录。")
        name = Path(item).name.lower()
        if name in (".env", "config.json", "credentials.json", ".git-credentials"):
            raise ToolExecutionError(f"{item} 可能包含密钥, 拒绝提交。")

    ask_confirmation(
        ctx,
        summary="创建 Git 提交",
        detail=f"提交信息: {message}\n文件: {', '.join(safe_paths)}",
        payload={"kind": "git_commit", "message": message, "paths": safe_paths},
    )

    code, out, err = _run_git(ctx, ["add", "--", *safe_paths])
    if code != 0:
        return ToolResult(ok=False, error=f"git add 失败: {err or out}")
    code, out, err = _run_git(ctx, ["commit", "-m", message])
    if code != 0:
        return ToolResult(ok=False, error=f"git commit 失败: {err or out}")
    ctx.write_log(f"[git_commit] {message}")
    return ToolResult(ok=True, data=out or "提交完成。")
