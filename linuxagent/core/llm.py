"""DeepSeek LLM 网关（OpenAI SDK 兼容）。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import APIConnectionError, APIError, OpenAI, RateLimitError
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from linuxagent.config import Settings, get_settings
from linuxagent.utils.errors import LLMCallError
from linuxagent.utils.helpers import safe_truncate
from linuxagent.utils.logger import mask_secret


class ToolCall(BaseModel):
    """LLM 产生的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """统一的 LLM 响应封装。"""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = ""
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMGateway:
    """DeepSeek / OpenAI 兼容聊天网关。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        logger.info(
            "LLMGateway ready: model={} base_url={} key={}",
            model,
            base_url,
            mask_secret(api_key),
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        **override: Any,
    ) -> LLMResponse:
        """调用聊天补全。

        Args:
            messages: OpenAI 格式 messages。
            tools: Function Calling schema 列表，None 则不启用 FC。
            tool_choice: ``auto`` / ``none`` 或具体 function 名。
            stream: 是否流式返回（MVP 暂不启用）。
            response_format: 可选 ``{"type": "json_object"}``，用于强制 JSON 输出。
            override: 其它透传给底层 OpenAI client 的参数。

        Returns:
            统一封装的 ``LLMResponse``。

        Raises:
            LLMCallError: 底层调用失败或响应解析失败。
        """
        preview_msg = safe_truncate(
            json.dumps(messages, ensure_ascii=False)[:1000], max_chars=500
        )[0]
        logger.debug("LLM in ({} msgs): {}", len(messages), preview_msg)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **override,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        if response_format:
            params["response_format"] = response_format
        if stream:
            params["stream"] = True

        try:
            resp = self._client.chat.completions.create(**params)
        except (APIConnectionError, RateLimitError):
            raise
        except APIError as e:
            raise LLMCallError(f"LLM API error: {e}") from e
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}") from e

        try:
            choice = resp.choices[0]
            message = choice.message
            content = getattr(message, "content", None)
            raw_tool_calls = getattr(message, "tool_calls", None) or []
        except (AttributeError, IndexError) as e:
            raise LLMCallError(f"Malformed LLM response: {e}") from e

        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            try:
                args_text = tc.function.arguments
                args_dict = json.loads(args_text) if args_text else {}
                if not isinstance(args_dict, dict):
                    args_dict = {}
            except (json.JSONDecodeError, AttributeError):
                args_dict = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args_dict))

        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else None
        finish_reason = choice.finish_reason or ""

        result = LLMResponse(
            content=content,
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            usage=usage,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )
        logger.info(
            "LLM out: finish={} content_len={} tool_calls={} usage={}",
            finish_reason,
            len(content) if content else 0,
            len(tool_calls),
            usage,
        )
        return result


def build_llm_gateway(settings: Settings | None = None) -> LLMGateway:
    """按 Settings 构建 LLMGateway。

    Raises:
        LLMCallError: 未配置 ``DEEPSEEK_API_KEY``。
    """
    s = settings or get_settings()
    api_key = s.deepseek_api_key.get_secret_value()
    if not api_key:
        raise LLMCallError("DEEPSEEK_API_KEY is empty; please set it in .env")
    return LLMGateway(
        api_key=api_key,
        base_url=s.llm.base_url,
        model=s.llm.model,
        timeout=s.llm.timeout_s,
        max_tokens=s.llm.max_tokens,
        temperature=s.llm.temperature,
    )
