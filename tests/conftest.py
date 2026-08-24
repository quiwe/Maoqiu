"""测试夹具。

全部测试离线运行: 用假的 OpenAI 客户端 + 临时工作目录,
不读取真实 config.json, 不发起网络请求。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maoqiu.config import Config  # noqa: E402
from maoqiu.tools.base import ToolContext  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """隔离环境变量与配置文件, 避免测试读到真实密钥。"""
    for name in ("MAOQIU_API_KEY", "OPENAI_API_KEY", "MAOQIU_BASE_URL", "OPENAI_BASE_URL", "MAOQIU_MODEL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAOQIU_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "hello.txt").write_text("第一行\n第二行\nTODO: 修一下\n", encoding="utf-8")
    (root / "app.py").write_text("def main():\n    return 1  # TODO: 补测试\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "note.md").write_text("# 标题\n内容\n", encoding="utf-8")
    return root


@pytest.fixture
def config(workspace, tmp_path):
    return Config(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model_name="test-model",
        workspace=str(workspace),
        confirm_mode="confirm",
        command_timeout=10,
        history_dir=str(tmp_path / "sessions"),
        log_file=str(tmp_path / "command.log"),
    )


@pytest.fixture
def ctx(config):
    """默认自动同意确认, 便于测试工具主流程。"""
    return ToolContext(config=config, confirm=lambda *_: True, log=lambda _msg: None)


@pytest.fixture
def deny_ctx(config):
    """拒绝一切确认, 用于验证确认门是否真的生效。"""
    return ToolContext(config=config, confirm=lambda *_: False, log=lambda _msg: None)


# ----------------------------------------------------------------------
# 假 OpenAI 客户端
# ----------------------------------------------------------------------


def make_tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def make_message(content: str = "", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("假客户端脚本已用尽, 说明循环调用次数超出预期。")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=item)])


class FakeClient:
    """最小化模拟 openai.OpenAI 的调用面。"""

    def __init__(self, script):
        self.chat = SimpleNamespace(completions=FakeCompletions(script))

    @property
    def calls(self):
        return self.chat.completions.calls


@pytest.fixture
def fake_client():
    return FakeClient
