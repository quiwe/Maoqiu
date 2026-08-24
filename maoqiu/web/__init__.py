"""Web 应用包。

这里只导出工厂函数, 不导出名为 `app` 的对象:
`maoqiu.web.app` 是子模块名, 若再绑定同名实例会遮蔽该模块,
导致 `import maoqiu.web.app` 拿到的是 FastAPI 实例而不是模块。
"""

from .app import build_default_app, create_app

__all__ = ["build_default_app", "create_app"]
