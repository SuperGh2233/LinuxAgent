"""文件检索与读取工具。"""

from __future__ import annotations

import shlex
from typing import Literal

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool


@tool(
    name="file_search",
    description=(
        "在指定目录下搜索文件或内容。search_type=name 时按文件名模糊匹配，"
        "content 时在文件内容中搜索文本。返回前 max_results 行结果。"
    ),
    risk_hint=RiskLevel.LOW,
)
def file_search(
    ctx: ToolContext,
    path: str,
    pattern: str,
    max_results: int = 20,
    search_type: Literal["name", "content"] = "name",
) -> ToolResult:
    max_results = min(max(max_results, 1), 100)
    safe_path = shlex.quote(path)
    safe_pat = shlex.quote(pattern)
    if search_type == "content":
        cmd = (
            f"grep -rIn --color=never -m 3 {safe_pat} {safe_path} 2>/dev/null | head -n {max_results}"
        )
    else:
        cmd = f"find {safe_path} -iname {safe_pat} 2>/dev/null | head -n {max_results}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="file_search")


@tool(
    name="file_read_tail",
    description="读取指定文件末尾 N 行（最大 1000）。适合查看日志文件。",
    risk_hint=RiskLevel.LOW,
)
def file_read_tail(ctx: ToolContext, path: str, lines: int = 50) -> ToolResult:
    lines = min(max(lines, 1), 1000)
    cmd = f"tail -n {lines} {shlex.quote(path)}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="file_read_tail")


@tool(
    name="file_stat",
    description="查看文件或目录的元信息（权限、大小、mtime、所有者）。",
    risk_hint=RiskLevel.LOW,
)
def file_stat(ctx: ToolContext, path: str) -> ToolResult:
    cmd = f"stat {shlex.quote(path)}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="file_stat")
