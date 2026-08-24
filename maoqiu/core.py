"""Agent 核心循环(阶段 5 与 6)。

以事件流的方式产出结果, 终端和 Web 共用同一套逻辑, 避免两份实现分叉。

修正旧版三个缺陷:
1. 旧版只处理 tool_calls[0], 这里遍历全部工具调用, 且每个调用都回填对应的 tool 消息
2. 旧版工具结果回填后, 第二次模型调用被注释掉, 导致工具轮次没有最终回复
3. 旧版内层 while True 没有上限, 这里限制最大工具轮数
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .errors import UpstreamError
from .security import redact
from .tools import parse_arguments, run_tool, tool_schemas
from .tools.base import ToolContext

MAX_TOOL_ROUNDS = 10


@dataclass
class Event:
    """流式事件。type 取值: text | tool_call | tool_result | error | done"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


def friendly_api_error(exc: Exception) -> str:
    """把 SDK 抛出的异常翻译成用户能理解的提示。"""
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()

    if "authenticationerror" in name.lower() or "401" in text or "invalid api key" in lowered:
        return "API KEY 无效或已过期。请检查配置中的密钥。"
    if "notfounderror" in name.lower() or "404" in text:
        return (
            "接口或模型不存在(404)。请确认模型名称是否正确, "
            "以及 base_url 是否需要以 /v1 结尾。"
        )
    if "ratelimit" in name.lower() or "429" in text:
        return "请求过于频繁或额度不足(429)。稍后再试, 或检查账户额度。"
    if "connection" in name.lower() or "timeout" in lowered:
        return f"无法连接到 API 服务。请检查网络和 base_url 是否可达。({name})"
    if "permissiondenied" in name.lower() or "403" in text:
        return "没有访问该模型的权限(403)。"
    if "badrequest" in name.lower() or "400" in text:
        if "tool" in lowered or "function" in lowered:
            return f"模型可能不支持 function calling, 或参数不被接受。原始信息: {redact(text)}"
        return f"请求参数被拒绝(400): {redact(text)}"
    return f"{name}: {redact(text)}"


def trim_history(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """裁剪历史, 保留 system 消息, 并避免把 tool 消息与其 assistant 调用切开。"""
    if len(messages) <= limit:
        return messages

    system = [m for m in messages[:1] if m.get("role") == "system"]
    rest = messages[len(system) :]
    keep = rest[-limit:]

    # 若首条被保留的消息是 tool 结果, 往前回溯到对应的 assistant 消息
    while keep and keep[0].get("role") == "tool":
        index = rest.index(keep[0])
        if index == 0:
            keep = keep[1:]
            break
        keep = rest[index - 1 :]
        if len(keep) > limit + 4:
            keep = keep[1:]
            break
    return system + keep


class Agent:
    """把配置、OpenAI 客户端和工具注册表组装成可运行的 Agent。"""

    def __init__(
        self,
        config: Config,
        client: Any,
        confirm: Callable[[str, str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.confirm = confirm

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        if not self.config.log_file:
            return
        try:
            path = Path(self.config.log_file)
            if not path.is_absolute():
                path = self.config.workspace_path / path
            path.parent.mkdir(parents=True, exist_ok=True)
            from datetime import datetime

            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {redact(message)}\n")
        except OSError:
            pass  # 日志失败不应影响主流程

    def tool_context(self) -> ToolContext:
        return ToolContext(config=self.config, confirm=self.confirm, log=self._log)

    # ------------------------------------------------------------------
    # 模型调用
    # ------------------------------------------------------------------
    def _complete(self, messages: list[dict[str, Any]]) -> Any:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                messages=trim_history(messages, self.config.max_history_messages),
                tools=tool_schemas(self.config),
            )
        except Exception as exc:  # noqa: BLE001 - SDK 异常种类多, 统一转换
            raise UpstreamError(friendly_api_error(exc)) from exc

        if not getattr(response, "choices", None):
            raise UpstreamError("模型没有返回任何内容, 请重试。")
        return response.choices[0].message

    @staticmethod
    def _assistant_dict(message: Any) -> dict[str, Any]:
        """把 SDK 的消息对象转成可序列化的 dict, 方便持久化。"""
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None) or "",
        }
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or "{}",
                    },
                }
                for call in tool_calls
            ]
        return payload

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run_turn(self, messages: list[dict[str, Any]], user_input: str) -> Iterator[Event]:
        """执行一轮对话。就地修改 messages, 并产出事件。"""
        messages.append({"role": "user", "content": user_input})

        for round_index in range(MAX_TOOL_ROUNDS):
            try:
                ai_message = self._complete(messages)
            except UpstreamError as exc:
                yield Event("error", {"message": str(exc)})
                return

            assistant_payload = self._assistant_dict(ai_message)
            tool_calls = assistant_payload.get("tool_calls")

            if not tool_calls:
                content = assistant_payload["content"] or "(模型返回了空内容)"
                messages.append({"role": "assistant", "content": content})
                yield Event("text", {"content": content})
                yield Event("done", {"rounds": round_index})
                return

            # 有工具调用: 先把 assistant 消息入历史, 再逐个执行
            messages.append(assistant_payload)
            if assistant_payload["content"]:
                yield Event("text", {"content": assistant_payload["content"]})

            ctx = self.tool_context()
            for call in tool_calls:
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"]

                try:
                    args = parse_arguments(raw_args)
                except Exception as exc:  # noqa: BLE001 - 参数非法转成工具错误回填
                    error_text = f"ERROR: {exc}"
                    messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": error_text}
                    )
                    yield Event(
                        "tool_result",
                        {"name": name, "ok": False, "error": str(exc), "call_id": call["id"]},
                    )
                    continue

                yield Event("tool_call", {"name": name, "arguments": args, "call_id": call["id"]})
                self._log(f"[tool] {name} {json.dumps(args, ensure_ascii=False)[:500]}")

                result = run_tool(name, ctx, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result.to_model_content(),
                    }
                )
                yield Event(
                    "tool_result",
                    {
                        "name": name,
                        "ok": result.ok,
                        "data": result.data,
                        "error": result.error,
                        "truncated": result.truncated,
                        "call_id": call["id"],
                    },
                )
            # 继续下一轮, 让模型根据工具结果生成最终回复

        # 达到轮数上限
        note = f"已连续调用工具 {MAX_TOOL_ROUNDS} 轮仍未得出结论, 已停止以避免死循环。"
        messages.append({"role": "assistant", "content": note})
        yield Event("error", {"message": note})
