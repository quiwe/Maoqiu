"""Agent 主循环测试。

重点覆盖旧版三个缺陷:
1. 只处理第一个 tool_call
2. 工具结果回填后没有再次调用模型, 导致没有最终回复
3. 工具循环没有上限
"""

from __future__ import annotations

from conftest import make_message, make_tool_call

from maoqiu.core import MAX_TOOL_ROUNDS, Agent, friendly_api_error, trim_history


def run(agent, messages, text):
    return list(agent.run_turn(messages, text))


def test_plain_conversation(config, fake_client):
    client = fake_client([make_message("你好, 我是毛球。")])
    agent = Agent(config, client)
    messages = [{"role": "system", "content": "sys"}]
    events = run(agent, messages, "你好")

    assert [e.type for e in events] == ["text", "done"]
    assert events[0].data["content"] == "你好, 我是毛球。"
    assert messages[-1] == {"role": "assistant", "content": "你好, 我是毛球。"}


def test_tool_call_then_final_reply(config, fake_client):
    """工具轮次必须产出最终回复(旧版这里是空的)。"""
    client = fake_client(
        [
            make_message("", [make_tool_call("call_1", "list_dir", '{"path": "."}')]),
            make_message("目录里有 hello.txt 和 app.py。"),
        ]
    )
    agent = Agent(config, client, confirm=lambda *_: True)
    messages = [{"role": "system", "content": "sys"}]
    events = run(agent, messages, "看看目录")

    types = [e.type for e in events]
    assert types == ["tool_call", "tool_result", "text", "done"]
    assert events[-2].data["content"].startswith("目录里有")
    assert client.chat.completions.calls.__len__() == 2


def test_parallel_tool_calls_each_get_result(config, fake_client):
    """多个工具调用都要执行, 且每个 tool_call_id 都要有对应的 tool 消息。"""
    client = fake_client(
        [
            make_message(
                "",
                [
                    make_tool_call("call_1", "read_file", '{"path": "hello.txt"}'),
                    make_tool_call("call_2", "read_file", '{"path": "app.py"}'),
                    make_tool_call("call_3", "list_dir", "{}"),
                ],
            ),
            make_message("三个文件都看过了。"),
        ]
    )
    agent = Agent(config, client, confirm=lambda *_: True)
    messages = [{"role": "system", "content": "sys"}]
    events = run(agent, messages, "读这些文件")

    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_results) == 3
    assert all(e.data["ok"] for e in tool_results)

    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert {m["tool_call_id"] for m in tool_messages} == {"call_1", "call_2", "call_3"}


def test_invalid_tool_arguments_reported(config, fake_client):
    """参数不是合法 JSON 时, 要回填错误而不是执行空命令。"""
    client = fake_client(
        [
            make_message("", [make_tool_call("call_1", "read_file", "{broken json")]),
            make_message("参数有问题, 我换个方式。"),
        ]
    )
    agent = Agent(config, client)
    messages = [{"role": "system", "content": "sys"}]
    events = run(agent, messages, "读文件")

    result = next(e for e in events if e.type == "tool_result")
    assert not result.data["ok"]
    tool_message = next(m for m in messages if m.get("role") == "tool")
    assert tool_message["content"].startswith("ERROR:")


def test_dangerous_command_returns_policy_error(config, fake_client):
    client = fake_client(
        [
            make_message("", [make_tool_call("c1", "run_command", '{"cmd": "rm -rf /"}')]),
            make_message("这个命令太危险, 我不会执行。"),
        ]
    )
    agent = Agent(config, client, confirm=lambda *_: True)
    messages = [{"role": "system", "content": "sys"}]
    events = run(agent, messages, "删掉所有东西")

    result = next(e for e in events if e.type == "tool_result")
    assert not result.data["ok"]
    assert "安全策略拒绝" in result.data["error"]


def test_tool_loop_capped(config, fake_client):
    """模型一直调工具时必须停下来, 不能无限循环。"""
    script = [
        make_message("", [make_tool_call(f"c{i}", "list_dir", "{}")])
        for i in range(MAX_TOOL_ROUNDS + 2)
    ]
    client = fake_client(script)
    agent = Agent(config, client, confirm=lambda *_: True)
    events = run(agent, [{"role": "system", "content": "sys"}], "循环吧")

    assert events[-1].type == "error"
    assert "已停止" in events[-1].data["message"]
    assert len(client.chat.completions.calls) == MAX_TOOL_ROUNDS


def test_api_error_becomes_event(config, fake_client):
    client = fake_client([RuntimeError("AuthenticationError: invalid api key")])
    agent = Agent(config, client)
    events = run(agent, [{"role": "system", "content": "sys"}], "你好")
    assert events[0].type == "error"
    assert "API KEY" in events[0].data["message"]


def test_empty_model_content(config, fake_client):
    client = fake_client([make_message("")])
    agent = Agent(config, client)
    events = run(agent, [{"role": "system", "content": "sys"}], "你好")
    assert events[0].type == "text"
    assert "空内容" in events[0].data["content"]


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------


def test_friendly_errors():
    assert "API KEY" in friendly_api_error(Exception("AuthenticationError 401 invalid api key"))
    assert "/v1" in friendly_api_error(Exception("NotFoundError: 404 model not found"))
    assert "429" in friendly_api_error(Exception("RateLimitError: 429"))


def test_friendly_error_redacts_secret():
    message = friendly_api_error(Exception('BadRequestError: 400 api_key="sk-abcdef1234567"'))
    assert "sk-abcdef1234567" not in message


def test_trim_history_keeps_system():
    messages = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": f"m{i}"} for i in range(50)]
    trimmed = trim_history(messages, 10)
    assert trimmed[0]["role"] == "system"
    assert len(trimmed) <= 11
    assert trimmed[-1]["content"] == "m49"


def test_trim_history_does_not_orphan_tool_message():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "assistant", "content": "done"},
    ]
    trimmed = trim_history(messages, 4)
    # 若首条是 tool 消息会让 API 报错, 这里必须避免
    assert trimmed[1]["role"] != "tool"
