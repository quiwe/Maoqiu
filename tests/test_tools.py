from __future__ import annotations

from maoqiu.tools import REGISTRY, run_tool, tool_schemas
from maoqiu.tools.base import parse_arguments


def call(name, ctx, **kwargs):
    return run_tool(name, ctx, kwargs)


# ----------------------------------------------------------------------
# 文件读写
# ----------------------------------------------------------------------


def test_list_dir(ctx):
    result = call("list_dir", ctx)
    assert result.ok
    assert "hello.txt" in result.data and "sub" in result.data


def test_read_file_numbered(ctx):
    result = call("read_file", ctx, path="hello.txt")
    assert result.ok and "1: 第一行" in result.data


def test_read_missing_file(ctx):
    result = call("read_file", ctx, path="nope.txt")
    assert not result.ok and "不存在" in result.error


def test_read_outside_workspace_denied(ctx):
    result = call("read_file", ctx, path="../../etc/passwd")
    assert not result.ok and result.meta.get("kind") == "policy"


def test_write_and_edit(ctx, workspace):
    assert call("write_file", ctx, path="new.txt", content="内容").ok
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "内容"
    assert call("edit_file", ctx, path="new.txt", old_string="内容", new_string="新内容").ok
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "新内容"


def test_write_rejected_when_user_declines(deny_ctx, workspace):
    result = call("write_file", deny_ctx, path="blocked.txt", content="x")
    assert not result.ok and "拒绝" in result.error
    assert not (workspace / "blocked.txt").exists()


def test_edit_requires_unique_match(ctx, workspace):
    (workspace / "dup.txt").write_text("a\na\n", encoding="utf-8")
    result = call("edit_file", ctx, path="dup.txt", old_string="a", new_string="b")
    assert not result.ok and "出现 2 次" in result.error
    assert call("edit_file", ctx, path="dup.txt", old_string="a", new_string="b", replace_all=True).ok


def test_delete_requires_confirmation(deny_ctx, workspace):
    result = call("delete_path", deny_ctx, path="hello.txt")
    assert not result.ok
    assert (workspace / "hello.txt").exists()


def test_delete_non_empty_dir_needs_recursive(ctx, workspace):
    result = call("delete_path", ctx, path="sub")
    assert not result.ok and "recursive=true" in result.error
    assert call("delete_path", ctx, path="sub", recursive=True).ok
    assert not (workspace / "sub").exists()


def test_cannot_delete_workspace_root(ctx):
    assert not call("delete_path", ctx, path=".").ok


def test_auto_mode_skips_confirmation(config, workspace):
    from maoqiu.tools.base import ToolContext

    config.confirm_mode = "auto"
    auto_ctx = ToolContext(config=config, confirm=None, log=None)
    assert call("write_file", auto_ctx, path="auto.txt", content="ok").ok


def test_delete_still_asks_in_auto_mode(config):
    """删除是唯一即使 auto 模式也必须确认的操作。"""
    from maoqiu.tools.base import ToolContext

    config.confirm_mode = "auto"
    auto_ctx = ToolContext(config=config, confirm=None, log=None)
    result = call("delete_path", auto_ctx, path="hello.txt")
    assert not result.ok and "确认" in result.error


# ----------------------------------------------------------------------
# 搜索
# ----------------------------------------------------------------------


def test_find_files(ctx):
    result = call("find_files", ctx, pattern="*.py")
    assert result.ok and "app.py" in result.data


def test_search_in_files(ctx):
    result = call("search_in_files", ctx, query="TODO")
    assert result.ok and "hello.txt" in result.data and "app.py" in result.data


def test_file_info(ctx):
    result = call("file_info", ctx, path="hello.txt")
    assert result.ok and "行数" in result.data


def test_make_dir(ctx, workspace):
    assert call("make_dir", ctx, path="a/b/c").ok
    assert (workspace / "a" / "b" / "c").is_dir()


# ----------------------------------------------------------------------
# 命令执行
# ----------------------------------------------------------------------


def test_run_readonly_command(ctx):
    result = call("run_command", ctx, cmd="echo hello")
    assert result.ok and "hello" in result.data and "退出码: 0" in result.data


def test_run_command_reports_failure(ctx):
    result = call("run_command", ctx, cmd="ls /definitely-not-here-xyz")
    assert not result.ok and result.meta["returncode"] != 0


def test_dangerous_command_blocked(ctx):
    result = call("run_command", ctx, cmd="rm -rf /")
    assert not result.ok and "安全策略拒绝" in result.error


def test_side_effect_command_blocked_without_confirmation(deny_ctx):
    result = call("run_command", deny_ctx, cmd="rm hello.txt")
    assert not result.ok and "拒绝" in result.error


def test_command_timeout(ctx):
    result = call("run_command", ctx, cmd="sleep 5", timeout=1)
    assert not result.ok and "超过 1 秒" in result.error


def test_command_runs_in_workspace(ctx, workspace):
    result = call("run_command", ctx, cmd="pwd")
    assert result.ok and str(workspace) in result.data


def test_output_truncated(ctx):
    ctx.config.max_output_chars = 600
    result = call("run_command", ctx, cmd="python3 -c \"print('x'*4000)\"")
    assert result.truncated


# ----------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------


def test_unknown_tool():
    from maoqiu.tools.base import ToolContext
    from maoqiu.config import Config

    result = run_tool("no_such_tool", ToolContext(config=Config()), {})
    assert not result.ok and "未知工具" in result.error


def test_network_tools_hidden_when_disabled(config):
    names = {s["function"]["name"] for s in tool_schemas(config)}
    assert "web_search" in names
    config.allow_network_tools = False
    names = {s["function"]["name"] for s in tool_schemas(config)}
    assert "web_search" not in names and "fetch_url" not in names


def test_network_tool_blocked_when_disabled(ctx):
    ctx.config.allow_network_tools = False
    result = call("fetch_url", ctx, url="https://example.com")
    assert not result.ok and "禁用" in result.error


def test_all_tools_have_schema():
    for name, tool in REGISTRY.items():
        schema = tool.schema()
        assert schema["function"]["name"] == name
        assert tool.description
        assert schema["function"]["parameters"]["type"] == "object"


def test_parse_arguments():
    assert parse_arguments('{"a": 1}') == {"a": 1}
    assert parse_arguments("") == {}
    assert parse_arguments({"b": 2}) == {"b": 2}


def test_parse_invalid_arguments():
    import pytest

    from maoqiu.errors import ToolExecutionError

    with pytest.raises(ToolExecutionError):
        parse_arguments("{not json")
