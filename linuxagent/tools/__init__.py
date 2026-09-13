"""工具集。导入本包会自动扫描同目录模块触发 ``@tool`` 注册。"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from linuxagent.tools.base import (
    RiskLevel,
    Tool,
    ToolContext,
    ToolResult,
    get_tool,
    invoke_tool,
    list_tool_names,
    openai_tools_schema,
    risk_max,
    tool,
)

__all__ = [
    "RiskLevel",
    "Tool",
    "ToolContext",
    "ToolResult",
    "get_tool",
    "invoke_tool",
    "list_tool_names",
    "openai_tools_schema",
    "risk_max",
    "tool",
]

_PACKAGE_DIR = Path(__file__).parent


def _autoload() -> None:
    """扫描同目录内所有子模块，导入触发工具装饰器注册。"""
    for _finder, mod_name, _ispkg in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        if mod_name in ("base",):
            continue
        importlib.import_module(f"{__name__}.{mod_name}")


_autoload()
