"""OpenAI 兼容客户端工厂。

单独抽出来, 便于测试时注入假客户端, 也便于以后换供应商。
"""

from __future__ import annotations

from typing import Any

from .config import Config, is_configured
from .errors import UpstreamError


def build_client(config: Config) -> Any:
    if not is_configured(config):
        raise UpstreamError("配置不完整, 请先设置 API KEY、base_url 和模型名称。")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise UpstreamError("缺少 openai 依赖, 请运行 pip install -r requirements.txt。") from exc

    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=120.0,
        max_retries=2,
    )


def test_connection(config: Config) -> tuple[bool, str]:
    """在保存配置前验证凭据是否可用。"""
    from .core import friendly_api_error

    try:
        client = build_client(config)
    except UpstreamError as exc:
        return False, str(exc)

    try:
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
    except Exception as exc:  # noqa: BLE001
        return False, friendly_api_error(exc)

    if not getattr(response, "choices", None):
        return False, "服务返回了空响应。"
    return True, f"连接成功, 模型 {config.model_name} 可用。"
