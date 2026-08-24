"""网络工具(阶段 8)。

所有出站请求都先经过 validate_url, 阻断内网与云元数据地址(防 SSRF)。
这两个工具标记为 risk="network", 可在配置里整体关闭。
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote_plus

from ..errors import ToolExecutionError
from ..security import redact, truncate, validate_url
from .base import ToolContext, ToolResult, optional_int, register, require_str

USER_AGENT = "Maoqiu-Agent/2.0 (local assistant)"


def _fetch(url: str, timeout: int) -> tuple[int, str, str]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可读提示
        raise ToolExecutionError("缺少 httpx 依赖, 请运行 pip install -r requirements.txt。") from exc

    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=False,  # 不跟随重定向, 防止绕过 SSRF 校验
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json"},
        )
    except Exception as exc:  # noqa: BLE001 - httpx 异常种类较多, 统一转换
        raise ToolExecutionError(f"请求失败: {type(exc).__name__}: {exc}") from exc

    if response.is_redirect:
        location = response.headers.get("location", "")
        raise ToolExecutionError(f"目标返回重定向到 {location}, 已停止。如需访问请直接提供最终地址。")
    return response.status_code, response.headers.get("content-type", ""), response.text


_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw: str) -> str:
    cleaned = _TAG_RE.sub(" ", raw)
    cleaned = _ANY_TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    return re.sub(r"\n\s*\n+", "\n\n", cleaned).strip()


@register(
    name="fetch_url",
    description="抓取一个网页或 API 地址的内容, 返回纯文本。只允许公网 http/https 地址。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整的 http/https 地址"},
            "timeout": {"type": "integer", "description": "超时秒数, 默认 20"},
        },
        "required": ["url"],
    },
    risk="network",
)
def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = validate_url(require_str(args, "url"))
    timeout = max(1, min(optional_int(args, "timeout", 20), 60))
    ctx.write_log(f"[fetch] {url}")

    status, content_type, text = _fetch(url, timeout)
    body = text if "json" in content_type or "text/plain" in content_type else _html_to_text(text)
    body, was_truncated = truncate(redact(body), ctx.config.max_output_chars)
    header = f"HTTP {status} | {content_type or '未知类型'} | {url}"
    if status >= 400:
        return ToolResult(ok=False, error=f"{header}\n\n{body}", truncated=was_truncated)
    return ToolResult(ok=True, data=f"{header}\n\n{body}", truncated=was_truncated)


@register(
    name="web_search",
    description="用 DuckDuckGo 搜索网络信息, 返回标题、摘要和链接。适合查询最新资料。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回结果条数, 默认 5"},
        },
        "required": ["query"],
    },
    risk="network",
)
def web_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = require_str(args, "query").strip()
    if not query:
        raise ToolExecutionError("搜索关键词不能为空。")
    limit = max(1, min(optional_int(args, "limit", 5), 15))
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    ctx.write_log(f"[search] {query}")

    status, _, raw = _fetch(validate_url(url), 25)
    if status >= 400:
        return ToolResult(ok=False, error=f"搜索失败, HTTP {status}。")

    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
        re.DOTALL | re.IGNORECASE,
    )

    items: list[str] = []
    for match in pattern.finditer(raw):
        title = _html_to_text(match.group("title") or "")
        href = html.unescape(match.group("href") or "")
        snippet = _html_to_text(match.group("snippet") or "")
        if not title or not href:
            continue
        entry = f"{len(items) + 1}. {title}\n   {href}"
        if snippet:
            entry += f"\n   {snippet[:300]}"
        items.append(entry)
        if len(items) >= limit:
            break

    if not items:
        return ToolResult(
            ok=True,
            data=f"没有解析到 {query} 的搜索结果。可以换个关键词, 或直接用 fetch_url 访问具体页面。",
        )
    body, was_truncated = truncate("\n".join(items), ctx.config.max_output_chars)
    return ToolResult(ok=True, data=f"{query} 的搜索结果:\n{body}", truncated=was_truncated)
