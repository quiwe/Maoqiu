"""统一的异常类型。

把不同层的失败区分开, 便于上层转换成用户能看懂的提示,
而不是直接把 Python traceback 抛给用户。
"""

from __future__ import annotations


class MaoqiuError(RuntimeError):
    """所有自定义异常的基类。"""


class PolicyDenied(MaoqiuError):
    """安全策略拒绝执行。"""


class SandboxViolation(MaoqiuError):
    """越出工作目录, 或触碰敏感文件。"""


class ToolExecutionError(MaoqiuError):
    """工具自身执行失败(参数错误、文件不存在等)。"""


class ConfirmationRejected(MaoqiuError):
    """用户拒绝了这次操作。"""


class UpstreamError(MaoqiuError):
    """调用大模型 API 失败。"""
