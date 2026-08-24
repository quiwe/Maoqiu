"""Git 工具测试: 只读查询放行, 提交需确认且拒绝危险参数。"""

from __future__ import annotations

import subprocess

import pytest

from maoqiu.tools import run_tool


@pytest.fixture
def git_repo(config):
    """在临时工作目录建一个真实的本地 Git 仓库。"""
    workspace = config.workspace_path

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    git("config", "commit.gpgsign", "false")
    git("add", "hello.txt")
    git("commit", "-q", "-m", "初始提交")
    return workspace


def test_git_status_reports_changes(git_repo, ctx):
    (git_repo / "hello.txt").write_text("改了内容\n", encoding="utf-8")
    result = run_tool("git_status", ctx, {})
    assert result.ok
    assert "hello.txt" in result.data


def test_git_diff_shows_modification(git_repo, ctx):
    (git_repo / "hello.txt").write_text("新的一行\n", encoding="utf-8")
    result = run_tool("git_diff", ctx, {})
    assert result.ok
    assert "新的一行" in result.data


def test_git_log_lists_commit(git_repo, ctx):
    result = run_tool("git_log", ctx, {"limit": 5})
    assert result.ok
    assert "初始提交" in result.data


def test_git_tools_require_repository(config, ctx):
    """非 Git 目录要给出清晰错误, 而不是崩溃。"""
    assert not (config.workspace_path / ".git").exists()
    result = run_tool("git_status", ctx, {})
    assert result.ok is False
    assert "不是 Git 仓库" in result.error


def test_git_commit_creates_commit(git_repo, ctx):
    (git_repo / "hello.txt").write_text("提交这一版\n", encoding="utf-8")
    result = run_tool(
        "git_commit", ctx, {"message": "更新 hello", "paths": ["hello.txt"]}
    )
    assert result.ok, result.error
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "更新 hello"


def test_git_commit_blocked_without_confirmation(git_repo, deny_ctx):
    (git_repo / "hello.txt").write_text("不该被提交\n", encoding="utf-8")
    result = run_tool("git_commit", deny_ctx, {"message": "x", "paths": ["hello.txt"]})
    assert result.ok is False
    assert "用户拒绝" in result.error


def test_git_commit_rejects_secret_files(git_repo, ctx):
    (git_repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    result = run_tool("git_commit", ctx, {"message": "add env", "paths": [".env"]})
    assert result.ok is False
    assert "可能包含密钥" in result.error


def test_git_commit_rejects_flag_like_path(git_repo, ctx):
    result = run_tool("git_commit", ctx, {"message": "x", "paths": ["--all"]})
    assert result.ok is False
    assert "不能以 - 开头" in result.error


def test_git_commit_rejects_path_outside_workspace(git_repo, ctx):
    result = run_tool("git_commit", ctx, {"message": "x", "paths": ["../outside.txt"]})
    assert result.ok is False
    assert "超出工作目录" in result.error


def test_git_commit_requires_message_and_paths(git_repo, ctx):
    empty_message = run_tool("git_commit", ctx, {"message": "   ", "paths": ["hello.txt"]})
    assert empty_message.ok is False
    assert "不能为空" in empty_message.error
    empty_paths = run_tool("git_commit", ctx, {"message": "x", "paths": []})
    assert empty_paths.ok is False
    assert "非空" in empty_paths.error


def test_git_diff_rejects_flag_like_path(git_repo, ctx):
    result = run_tool("git_diff", ctx, {"path": "--output=/tmp/x"})
    assert result.ok is False
    assert "不能以 - 开头" in result.error
