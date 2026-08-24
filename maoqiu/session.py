"""会话持久化(阶段 5)。

一个会话一个 JSON 文件, 采用临时文件加 os.replace 的原子写, 避免写坏历史。
完整记录存磁盘, 发给模型时再按 max_history_messages 裁剪。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    title: str = "新对话"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            title=str(data.get("title") or "新对话"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            messages=list(data.get("messages") or []),
        )

    def visible_messages(self) -> list[dict[str, Any]]:
        """给前端展示用: 去掉 system 消息。"""
        return [m for m in self.messages if m.get("role") != "system"]

    def derive_title(self) -> str:
        for message in self.messages:
            if message.get("role") == "user" and message.get("content"):
                text = str(message["content"]).strip().splitlines()[0]
                return text[:40] or "新对话"
        return "新对话"


class SessionStore:
    """会话仓库。save_history=False 时只保留在内存中。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._memory: dict[str, Session] = {}

    @property
    def directory(self) -> Path:
        path = Path(self.config.history_dir).expanduser()
        if not path.is_absolute():
            path = self.config.workspace_path / path
        return path

    def _path(self, session_id: str) -> Path:
        if not _ID_RE.match(session_id):
            raise ValueError("会话 ID 格式非法。")
        return self.directory / f"{session_id}.json"

    # ------------------------------------------------------------------
    def create(self, title: str | None = None) -> Session:
        session = Session(id=uuid.uuid4().hex[:12], title=title or "新对话")
        session.messages.append(
            {"role": "system", "content": self.config.effective_system_prompt()}
        )
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        session.updated_at = _now()
        self._memory[session.id] = session
        if not self.config.save_history:
            return
        try:
            path = self._path(session.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)  # 原子替换, 避免中断导致文件损坏
        except (OSError, ValueError):
            pass  # 持久化失败不影响当前会话继续使用

    def get(self, session_id: str) -> Session | None:
        if session_id in self._memory:
            return self._memory[session_id]
        if not self.config.save_history:
            return None
        try:
            path = self._path(session_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        session = Session.from_dict(data)
        self._memory[session.id] = session
        return session

    def list(self) -> list[dict[str, Any]]:
        sessions: dict[str, Session] = dict(self._memory)
        if self.config.save_history and self.directory.exists():
            for file_path in self.directory.glob("*.json"):
                if file_path.stem in sessions:
                    continue
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                session = Session.from_dict(data)
                sessions[session.id] = session
        items = [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": len(s.visible_messages()),
            }
            for s in sessions.values()
        ]
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def delete(self, session_id: str) -> bool:
        existed = self._memory.pop(session_id, None) is not None
        try:
            path = self._path(session_id)
        except ValueError:
            return existed
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError:
                return existed
        return existed

    @staticmethod
    def rollback_unconfirmed_turn(session: Session) -> bool:
        """回滚上一轮"因未确认而失败"的工具轮次。

        场景: 用户在 Web 上点"确认后重试"。如果把被拒绝的 tool 结果留在历史里,
        模型会认为该步骤已经处理过, 从而直接给出结论, 工具其实从未执行。
        这里把最后一组 assistant(tool_calls) + tool 消息和触发它的 user 消息一起移除,
        让重试请求重新走一遍完整的工具调用。

        返回是否发生了回滚。
        """
        messages = session.messages
        last_assistant = -1
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "assistant" and messages[index].get("tool_calls"):
                last_assistant = index
                break
        if last_assistant == -1:
            return False

        tail = messages[last_assistant + 1 :]
        if not tail:
            return False
        # 工具失败后 core 还会追加一条模型总结, 所以允许尾部结构为
        # tool 消息 + 可选 assistant 总结, 不能只要求全是 tool。
        tail_without_summary = tail
        if tail_without_summary and tail_without_summary[-1].get("role") == "assistant":
            tail_without_summary = tail_without_summary[:-1]
        if not tail_without_summary or not all(
            m.get("role") == "tool" for m in tail_without_summary
        ):
            return False
        # 只在确实被确认门拦下时回滚, 普通工具失败要保留给模型参考。
        blocked = any(
            "用户拒绝了操作" in str(m.get("content") or "")
            or "需要确认" in str(m.get("content") or "")
            for m in tail_without_summary
        )
        if not blocked:
            return False

        cut = last_assistant
        if cut > 0 and messages[cut - 1].get("role") == "user":
            cut -= 1
        del messages[cut:]
        return True

    def clear_messages(self, session: Session) -> Session:
        """清空对话内容但保留会话与系统提示。"""
        session.messages = [
            {"role": "system", "content": self.config.effective_system_prompt()}
        ]
        session.title = "新对话"
        self.save(session)
        return session

    def export_markdown(self, session: Session) -> str:
        lines = [f"# {session.title}", "", f"创建时间: {session.created_at}", ""]
        for message in session.visible_messages():
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                lines += ["## 我", "", str(content), ""]
            elif role == "assistant":
                if message.get("tool_calls"):
                    names = ", ".join(
                        c.get("function", {}).get("name", "?") for c in message["tool_calls"]
                    )
                    lines += [f"## 毛球 (调用工具: {names})", ""]
                    if content:
                        lines += [str(content), ""]
                else:
                    lines += ["## 毛球", "", str(content), ""]
            elif role == "tool":
                lines += ["<details><summary>工具输出</summary>", "", "```", str(content)[:2000], "```", "", "</details>", ""]
        return "\n".join(lines)
