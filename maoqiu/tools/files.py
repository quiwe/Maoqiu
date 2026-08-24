"""文件操作工具(阶段 3)。

把常用能力做成独立工具, 模型不必再拼 PowerShell 或 shell 命令,
执行更稳定, 也更容易做权限控制与错误处理。
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..errors import ToolExecutionError
from ..security import redact, resolve_in_workspace, truncate
from .base import (
    ToolContext,
    ToolResult,
    ask_confirmation,
    optional_int,
    register,
    require_str,
)

# 跳过这些目录, 避免搜索时被依赖和构建产物淹没
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

MAX_READ_BYTES = 2 * 1024 * 1024


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def _rel(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace)) or "."
    except ValueError:
        return str(path)


def _iter_files(root: Path, workspace: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for filename in filenames:
            yield Path(dirpath) / filename


@register(
    name="list_dir",
    description="列出目录内容, 显示文件大小和修改时间。默认列出工作目录。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的路径, 省略则为工作目录根"},
            "recursive": {"type": "boolean", "description": "是否递归列出子目录, 默认 false"},
            "limit": {"type": "integer", "description": "最多返回多少条, 默认 200"},
        },
    },
)
def list_dir(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, args.get("path") or ".", allow_sensitive=True)
    if not target.exists():
        raise ToolExecutionError(f"目录不存在: {_rel(target, workspace)}")
    if not target.is_dir():
        raise ToolExecutionError(f"{_rel(target, workspace)} 不是目录, 读取文件请用 read_file。")

    limit = max(1, min(optional_int(args, "limit", 200), 1000))
    recursive = bool(args.get("recursive", False))

    entries: list[str] = []
    if recursive:
        for file_path in _iter_files(target, workspace):
            entries.append(f"{_rel(file_path, workspace)}  {_human_size(file_path.stat().st_size)}")
            if len(entries) >= limit:
                break
    else:
        try:
            children = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            raise ToolExecutionError(f"无法读取目录: {exc}") from exc
        for child in children[:limit]:
            try:
                stat = child.stat()
            except OSError:
                entries.append(f"{child.name}  (无法读取属性)")
                continue
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            if child.is_dir():
                entries.append(f"[目录] {child.name}/  {mtime}")
            else:
                entries.append(f"[文件] {child.name}  {_human_size(stat.st_size)}  {mtime}")

    if not entries:
        return ToolResult(ok=True, data=f"{_rel(target, workspace)} 是空目录。")
    header = f"{_rel(target, workspace)} 共 {len(entries)} 项:"
    return ToolResult(ok=True, data=header + "\n" + "\n".join(entries))


@register(
    name="read_file",
    description="读取文本文件内容, 返回带行号的文本。支持通过 offset/limit 读取大文件的片段。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "description": "起始行号(从 1 开始), 默认 1"},
            "limit": {"type": "integer", "description": "最多读取多少行, 默认 500"},
        },
        "required": ["path"],
    },
)
def read_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"))
    if not target.exists():
        raise ToolExecutionError(f"文件不存在: {_rel(target, workspace)}")
    if target.is_dir():
        raise ToolExecutionError(f"{_rel(target, workspace)} 是目录, 请用 list_dir。")

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolExecutionError(
            f"文件过大({_human_size(size)}), 超过 {_human_size(MAX_READ_BYTES)} 上限。"
            "请用 search_in_files 定位需要的片段。"
        )

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolExecutionError(f"读取失败: {exc}") from exc

    lines = content.splitlines()
    offset = max(1, optional_int(args, "offset", 1))
    limit = max(1, min(optional_int(args, "limit", 500), 3000))
    selected = lines[offset - 1 : offset - 1 + limit]
    if not selected:
        return ToolResult(ok=True, data=f"{_rel(target, workspace)} 共 {len(lines)} 行, 指定范围内没有内容。")

    numbered = "\n".join(f"{offset + i}: {line}" for i, line in enumerate(selected))
    numbered, was_truncated = truncate(numbered, ctx.config.max_output_chars)
    end = offset + len(selected) - 1
    header = f"{_rel(target, workspace)} (第 {offset}-{end} 行, 共 {len(lines)} 行):"
    return ToolResult(ok=True, data=f"{header}\n{numbered}", truncated=was_truncated)


@register(
    name="write_file",
    description="创建新文件或整体覆盖已有文件。覆盖已有文件前需要用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    },
    risk="write",
)
def write_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"))
    content = require_str(args, "content")
    rel = _rel(target, workspace)
    exists = target.exists()

    if exists and target.is_dir():
        raise ToolExecutionError(f"{rel} 是目录, 无法写入。")

    action = "覆盖文件" if exists else "创建文件"
    ask_confirmation(
        ctx,
        summary=f"{action}: {rel}",
        detail=f"将写入 {len(content)} 字符" + ("(原内容会被替换)" if exists else ""),
        payload={"path": rel, "kind": "write", "bytes": len(content)},
    )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolExecutionError(f"写入失败: {exc}") from exc

    ctx.write_log(f"[write] {rel} ({len(content)} chars)")
    return ToolResult(ok=True, data=f"已{action}: {rel}, 共 {len(content)} 字符。")


@register(
    name="edit_file",
    description=(
        "把文件中的指定文本替换为新文本, 适合小范围精确修改。"
        "old_string 必须在文件中唯一出现, 否则会报错。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要被替换的原文本"},
            "new_string": {"type": "string", "description": "替换后的新文本, 传空字符串表示删除"},
            "replace_all": {"type": "boolean", "description": "是否替换全部匹配, 默认 false"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    risk="write",
)
def edit_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"))
    old_string = require_str(args, "old_string")
    new_string = require_str(args, "new_string")
    replace_all = bool(args.get("replace_all", False))
    rel = _rel(target, workspace)

    if not target.exists():
        raise ToolExecutionError(f"文件不存在: {rel}")
    if not old_string:
        raise ToolExecutionError("old_string 不能为空。")

    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolExecutionError(f"读取失败: {exc}") from exc

    count = content.count(old_string)
    if count == 0:
        raise ToolExecutionError(f"在 {rel} 中找不到指定文本, 请先用 read_file 确认原文。")
    if count > 1 and not replace_all:
        raise ToolExecutionError(
            f"指定文本在 {rel} 中出现 {count} 次。请提供更精确的 old_string, 或设置 replace_all=true。"
        )

    ask_confirmation(
        ctx,
        summary=f"修改文件: {rel}",
        detail=f"替换 {count if replace_all else 1} 处内容",
        payload={"path": rel, "kind": "edit", "occurrences": count},
    )

    updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise ToolExecutionError(f"写入失败: {exc}") from exc

    ctx.write_log(f"[edit] {rel} ({count if replace_all else 1} 处)")
    return ToolResult(ok=True, data=f"已修改 {rel}, 替换 {count if replace_all else 1} 处。")


@register(
    name="delete_path",
    description="删除文件或空目录。始终需要用户确认。删除非空目录需显式设置 recursive=true。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的路径"},
            "recursive": {"type": "boolean", "description": "是否递归删除目录, 默认 false"},
        },
        "required": ["path"],
    },
    risk="write",
)
def delete_path(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"))
    recursive = bool(args.get("recursive", False))
    rel = _rel(target, workspace)

    if target == workspace:
        raise ToolExecutionError("不允许删除工作目录本身。")
    if not target.exists():
        raise ToolExecutionError(f"路径不存在: {rel}")

    if target.is_dir():
        children = list(target.iterdir())
        if children and not recursive:
            raise ToolExecutionError(
                f"{rel} 非空(含 {len(children)} 项)。确认要整体删除请设置 recursive=true。"
            )
        detail = f"目录及其中 {len(children)} 项内容将被删除, 此操作不可撤销"
    else:
        detail = f"文件大小 {_human_size(target.stat().st_size)}, 此操作不可撤销"

    # 删除始终要问, 即使在 auto 模式下也不例外。
    if ctx.confirm is None:
        raise ToolExecutionError(f"删除 {rel} 需要确认, 但当前没有确认通道。")
    if not ctx.confirm(f"删除: {rel}", detail, {"path": rel, "kind": "delete"}):
        raise ToolExecutionError(f"用户拒绝删除 {rel}。")

    try:
        if target.is_dir():
            shutil.rmtree(target) if recursive else target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        raise ToolExecutionError(f"删除失败: {exc}") from exc

    ctx.write_log(f"[delete] {rel}")
    return ToolResult(ok=True, data=f"已删除 {rel}。")


@register(
    name="make_dir",
    description="创建目录, 自动创建缺失的父级目录。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "目录路径"}},
        "required": ["path"],
    },
    risk="write",
)
def make_dir(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"))
    rel = _rel(target, workspace)
    if target.exists():
        return ToolResult(ok=True, data=f"目录已存在: {rel}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolExecutionError(f"创建目录失败: {exc}") from exc
    return ToolResult(ok=True, data=f"已创建目录 {rel}。")


@register(
    name="find_files",
    description="按文件名或通配符查找文件, 例如 *.py 或 test_*.json。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名通配符, 如 *.py"},
            "path": {"type": "string", "description": "搜索起始目录, 默认工作目录"},
            "limit": {"type": "integer", "description": "最多返回条数, 默认 100"},
        },
        "required": ["pattern"],
    },
)
def find_files(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    pattern = require_str(args, "pattern")
    root = resolve_in_workspace(workspace, args.get("path") or ".", allow_sensitive=True)
    limit = max(1, min(optional_int(args, "limit", 100), 500))

    if not root.exists():
        raise ToolExecutionError(f"目录不存在: {_rel(root, workspace)}")

    matches: list[str] = []
    for file_path in _iter_files(root, workspace):
        if fnmatch.fnmatch(file_path.name, pattern):
            matches.append(_rel(file_path, workspace))
            if len(matches) >= limit:
                break

    if not matches:
        return ToolResult(ok=True, data=f"没有找到匹配 {pattern} 的文件。")
    return ToolResult(ok=True, data=f"匹配 {pattern} 的文件({len(matches)} 个):\n" + "\n".join(matches))


@register(
    name="search_in_files",
    description="在文件内容中搜索关键词或正则, 返回文件名、行号和匹配行。用于快速定位代码。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要搜索的文本或正则表达式"},
            "path": {"type": "string", "description": "搜索起始目录, 默认工作目录"},
            "glob": {"type": "string", "description": "限定文件名通配符, 如 *.py"},
            "limit": {"type": "integer", "description": "最多返回匹配数, 默认 80"},
        },
        "required": ["query"],
    },
)
def search_in_files(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    import re

    workspace = ctx.config.workspace_path
    query = require_str(args, "query")
    root = resolve_in_workspace(workspace, args.get("path") or ".", allow_sensitive=True)
    name_glob = args.get("glob") or "*"
    limit = max(1, min(optional_int(args, "limit", 80), 400))

    try:
        regex = re.compile(query)
    except re.error:
        regex = re.compile(re.escape(query))

    results: list[str] = []
    for file_path in _iter_files(root, workspace):
        if not fnmatch.fnmatch(file_path.name, name_glob):
            continue
        try:
            if file_path.stat().st_size > MAX_READ_BYTES:
                continue
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                snippet = line.strip()[:200]
                results.append(f"{_rel(file_path, workspace)}:{lineno}: {snippet}")
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break

    if not results:
        return ToolResult(ok=True, data=f"没有找到包含 {query} 的内容。")
    body, was_truncated = truncate(redact("\n".join(results)), ctx.config.max_output_chars)
    return ToolResult(ok=True, data=f"匹配 {query} 的结果({len(results)} 条):\n{body}", truncated=was_truncated)


@register(
    name="file_info",
    description="查看文件或目录的大小、修改时间、类型等元信息。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "路径"}},
        "required": ["path"],
    },
)
def file_info(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workspace = ctx.config.workspace_path
    target = resolve_in_workspace(workspace, require_str(args, "path"), allow_sensitive=True)
    if not target.exists():
        raise ToolExecutionError(f"路径不存在: {_rel(target, workspace)}")
    stat = target.stat()
    lines = [
        f"路径: {_rel(target, workspace)}",
        f"类型: {'目录' if target.is_dir() else '文件'}",
        f"大小: {_human_size(stat.st_size)}",
        f"修改时间: {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}",
    ]
    if target.is_file():
        try:
            line_count = len(target.read_text(encoding="utf-8", errors="ignore").splitlines())
            lines.append(f"行数: {line_count}")
        except OSError:
            pass
    return ToolResult(ok=True, data="\n".join(lines))
