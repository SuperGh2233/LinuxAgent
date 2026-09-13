"""网络相关工具。"""

from __future__ import annotations

import shlex

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool


@tool(
    name="network_ping",
    description="对目标主机 ping N 次（默认 4 次，最大 10 次，每包超时 3 秒）。",
    risk_hint=RiskLevel.LOW,
)
def network_ping(ctx: ToolContext, target: str, count: int = 4) -> ToolResult:
    count = min(max(count, 1), 10)
    cmd = f"ping -c {count} -W 3 {shlex.quote(target)}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="network_ping")


@tool(
    name="network_route",
    description="查看系统路由表（ip route）。",
    risk_hint=RiskLevel.LOW,
)
def network_route(ctx: ToolContext) -> ToolResult:
    return ToolResult.from_exec(ctx.executor.run("ip route"), tool_name="network_route")


@tool(
    name="network_interfaces",
    description="列出网络接口与 IP 地址（ip -brief addr / ifconfig）。",
    risk_hint=RiskLevel.LOW,
)
def network_interfaces(ctx: ToolContext) -> ToolResult:
    return ToolResult.from_exec(
        ctx.executor.run("ip -brief addr 2>/dev/null || ifconfig"),
        tool_name="network_interfaces",
    )
