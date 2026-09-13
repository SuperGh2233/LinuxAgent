"""磁盘相关工具。"""

from __future__ import annotations

import shlex

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool


@tool(
    name="disk_usage",
    description=(
        "查询指定路径的磁盘使用情况（df），返回容量、已用、可用和挂载点。"
        "常用于排查磁盘占满、检查剩余空间等场景。"
    ),
    risk_hint=RiskLevel.LOW,
)
def disk_usage(
    ctx: ToolContext,
    path: str = "/",
    human_readable: bool = True,
) -> ToolResult:
    flag = "-h" if human_readable else ""
    cmd = f"df {flag} {shlex.quote(path)}".strip()
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="disk_usage")


@tool(
    name="disk_inode",
    description="查询指定路径的 inode 使用率（df -i），用于排查'磁盘未满但报空间不足'。",
    risk_hint=RiskLevel.LOW,
)
def disk_inode(ctx: ToolContext, path: str = "/") -> ToolResult:
    cmd = f"df -i {shlex.quote(path)}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="disk_inode")
