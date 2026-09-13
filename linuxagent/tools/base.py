"""工具基类、注册器、装饰器。

核心能力：
- ``@tool`` 装饰器：把普通函数注册为 OpenAI Function Calling 可用工具，
  并从签名自动生成 JSON Schema
- ``invoke_tool``：按名字 + 参数字典调度工具，内置 Pydantic 参数校验，
  任何校验/执行错误都收敛为 ``ToolResult.from_error``，不向外抛
"""

from __future__ import annotations

import inspect
import json
import typing
from enum import Enum
from typing import Any, Callable, Literal, get_args, get_origin, get_type_hints

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from linuxagent.executor.result import ExecResult


class RiskLevel(str, Enum):
    """风险等级。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.BLOCKED: 3,
}


def risk_max(*levels: RiskLevel) -> RiskLevel:
    """取多个风险等级中的最高者。"""
    if not levels:
        return RiskLevel.LOW
    return max(levels, key=lambda lv: _RISK_RANK[lv])


class ToolContext(BaseModel):
    """工具执行期注入的运行时上下文。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    executor: Any
    session_id: str
    env_snapshot: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果。"""

    tool_name: str
    success: bool
    content: str
    data: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_exec(
        cls,
        exec_result: ExecResult,
        tool_name: str,
        extra: dict[str, Any] | None = None,
    ) -> ToolResult:
        """把 ExecResult 包装为 ToolResult。"""
        content = exec_result.to_brief()
        data: dict[str, Any] = {
            "command": exec_result.command,
            "return_code": exec_result.return_code,
            "duration_ms": exec_result.duration_ms,
            "truncated": exec_result.truncated,
            "stdout": exec_result.stdout,
            "stderr": exec_result.stderr,
        }
        if extra:
            data.update(extra)
        err: str | None = None
        if not exec_result.success:
            err = (exec_result.stderr.strip() or f"exit_code={exec_result.return_code}")[:512]
        return cls(
            tool_name=tool_name,
            success=exec_result.success,
            content=content,
            data=data,
            error=err,
        )

    @classmethod
    def from_error(cls, tool_name: str, message: str) -> ToolResult:
        """构造一个失败的 ToolResult（供参数校验、找不到工具等场景使用）。"""
        return cls(
            tool_name=tool_name,
            success=False,
            content=json.dumps({"error": message}, ensure_ascii=False),
            data=None,
            error=message,
        )


class Tool(BaseModel):
    """已注册的工具描述。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    risk_hint: RiskLevel
    parameters: dict[str, Any]
    handler: Callable[..., ToolResult]

    def to_openai_schema(self) -> dict[str, Any]:
        """输出 OpenAI Function Calling 格式的 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """把 Python 类型注解映射为 JSON Schema 片段。"""
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    origin = get_origin(annotation)
    if origin is Literal:
        values = list(get_args(annotation))
        schema: dict[str, Any] = {"enum": values}
        if all(isinstance(v, bool) for v in values):
            schema["type"] = "boolean"
        elif all(isinstance(v, int) for v in values):
            schema["type"] = "integer"
        elif all(isinstance(v, (int, float)) for v in values):
            schema["type"] = "number"
        else:
            schema["type"] = "string"
        return schema
    if origin is typing.Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_schema(non_none[0])
    return {"type": "string"}


def _build_schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    """从函数签名生成 OpenAI 的 ``parameters`` JSON Schema。"""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname == "ctx":
            continue
        ann = hints.get(pname, str)
        prop = _python_type_to_schema(ann)
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = param.default
        properties[pname] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def tool(
    name: str,
    description: str,
    risk_hint: RiskLevel = RiskLevel.LOW,
) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
    """装饰器：把函数注册为 LLM 可调用工具。

    Args:
        name: 工具名称（与函数名可不同；LLM 将以此名调用）。
        description: 给 LLM 看的工具说明。
        risk_hint: 工具自身的风险提示，风险引擎会结合它综合判定。
    """

    def decorator(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        params_schema = _build_schema_from_signature(fn)
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            risk_hint=risk_hint,
            parameters=params_schema,
            handler=fn,
        )
        logger.debug("tool registered: {} risk={}", name, risk_hint.value)
        return fn

    return decorator


def openai_tools_schema() -> list[dict[str, Any]]:
    """返回所有已注册工具的 OpenAI schema。"""
    return [t.to_openai_schema() for t in _REGISTRY.values()]


def list_tool_names() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def invoke_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """按名字调用工具。任何错误都收敛为失败的 ToolResult。"""
    tool_obj = _REGISTRY.get(name)
    if tool_obj is None:
        return ToolResult.from_error(name, f"Tool '{name}' not found")
    try:
        validated = _validate_arguments(tool_obj, arguments)
    except ValidationError as e:
        return ToolResult.from_error(name, f"Invalid arguments: {e.errors()}")
    try:
        return tool_obj.handler(ctx, **validated)
    except Exception as e:
        logger.exception("tool {} raised", name)
        return ToolResult.from_error(name, f"Tool execution error: {e}")


def _validate_arguments(tool_obj: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
    """用 ``pydantic.create_model`` 按 handler 签名做严格校验。"""
    sig = inspect.signature(tool_obj.handler)
    try:
        hints = get_type_hints(tool_obj.handler)
    except Exception:
        hints = {}
    fields: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname == "ctx":
            continue
        ann = hints.get(pname, str)
        if param.default is inspect.Parameter.empty:
            fields[pname] = (ann, ...)
        else:
            fields[pname] = (ann, param.default)
    model_cls = create_model(f"{tool_obj.name}_Args", **fields)  # type: ignore[call-overload]
    instance = model_cls(**arguments)
    return instance.model_dump()
