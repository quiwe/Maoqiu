"""毛球 AI 助手启动入口。

保留 `python agent.py` 的用法, 实际逻辑在 maoqiu 包内:
- maoqiu/config.py    配置
- maoqiu/security.py  安全策略与路径沙箱
- maoqiu/tools/       工具集合
- maoqiu/core.py      Agent 主循环
- maoqiu/cli.py       终端界面
- maoqiu/web/         Web 界面

用法:
    python agent.py              进入终端模式
    python agent.py --web        启动 Web 界面
    python agent.py --help       查看全部参数
"""

import sys

from maoqiu.cli import main

if __name__ == "__main__":
    sys.exit(main())
