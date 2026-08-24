"""工具集合。

导入各子模块以触发 @register 注册, 对外暴露注册表相关函数。
"""

from . import files, git, shell, web  # noqa: F401 - 导入即注册
from .base import (
    REGISTRY,
    Tool,
    ToolContext,
    ToolResult,
    parse_arguments,
    run_tool,
    tool_schemas,
)

__all__ = [
    "REGISTRY",
    "Tool",
    "ToolContext",
    "ToolResult",
    "parse_arguments",
    "run_tool",
    "tool_schemas",
]
