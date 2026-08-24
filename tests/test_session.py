"""会话持久化与确认重试回滚测试。"""

from __future__ import annotations

from maoqiu.session import Session, SessionStore


def make_blocked_history() -> Session:
    """构造一段"因未确认而失败"的历史。"""
    return Session(
        id="abcdef123456",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "帮我写文件"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ERROR: 用户拒绝了操作: 创建文件: a.txt"},
        ],
    )


def test_rollback_removes_blocked_turn(config):
    store = SessionStore(config)
    session = make_blocked_history()
    assert store.rollback_unconfirmed_turn(session) is True
    # 回滚后应只剩 system, 让重试重新走一遍工具调用
    assert [m["role"] for m in session.messages] == ["system"]


def test_rollback_removes_blocked_turn_with_summary(config):
    """core 会在工具失败后保留模型总结, 重试时也必须一并回滚。"""
    store = SessionStore(config)
    session = make_blocked_history()
    session.messages.append({"role": "assistant", "content": "需要确认后才能继续。"})
    assert store.rollback_unconfirmed_turn(session) is True
    assert [m["role"] for m in session.messages] == ["system"]


def test_rollback_keeps_normal_tool_failure(config):
    """普通工具失败要保留给模型参考, 不能被回滚掉。"""
    store = SessionStore(config)
    session = make_blocked_history()
    session.messages[-1]["content"] = "ERROR: 文件不存在: a.txt"
    assert store.rollback_unconfirmed_turn(session) is False
    assert len(session.messages) == 4


def test_rollback_noop_without_tool_turn(config):
    store = SessionStore(config)
    session = Session(
        id="abcdef123456",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ],
    )
    assert store.rollback_unconfirmed_turn(session) is False
    assert len(session.messages) == 3


def test_rollback_keeps_completed_tool_turn(config):
    store = SessionStore(config)
    session = make_blocked_history()
    session.messages[-1]["content"] = "已创建文件 a.txt"
    assert store.rollback_unconfirmed_turn(session) is False


# ----------------------------------------------------------------------
# 持久化
# ----------------------------------------------------------------------


def test_create_includes_system_prompt(config):
    store = SessionStore(config)
    session = store.create()
    assert session.messages[0]["role"] == "system"
    assert str(config.workspace_path) in session.messages[0]["content"]


def test_save_and_reload(config):
    store = SessionStore(config)
    session = store.create()
    session.messages.append({"role": "user", "content": "第一条"})
    store.save(session)

    fresh = SessionStore(config)
    loaded = fresh.get(session.id)
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "第一条"


def test_memory_only_when_history_disabled(config):
    config.save_history = False
    store = SessionStore(config)
    session = store.create()
    assert not (config.workspace_path / ".maoqiu" / "sessions").exists()
    assert store.get(session.id) is session


def test_invalid_session_id_rejected(config):
    store = SessionStore(config)
    assert store.get("../../etc/passwd") is None


def test_derive_title_and_clear(config):
    store = SessionStore(config)
    session = store.create()
    session.messages.append({"role": "user", "content": "查看一下项目结构"})
    assert session.derive_title() == "查看一下项目结构"

    store.clear_messages(session)
    assert [m["role"] for m in session.messages] == ["system"]
    assert session.title == "新对话"


def test_export_markdown_contains_dialogue(config):
    store = SessionStore(config)
    session = store.create()
    session.messages.append({"role": "user", "content": "你好"})
    session.messages.append({"role": "assistant", "content": "你好, 我是毛球"})
    markdown = store.export_markdown(session)
    assert "## 我" in markdown and "## 毛球" in markdown
    assert "sys" not in markdown


def test_list_sorted_and_counted(config):
    store = SessionStore(config)
    first = store.create()
    first.messages.append({"role": "user", "content": "第一个会话"})
    store.save(first)
    second = store.create()
    second.messages.append({"role": "user", "content": "第二个会话"})
    store.save(second)

    items = store.list()
    assert len(items) >= 2
    assert all("message_count" in item for item in items)
