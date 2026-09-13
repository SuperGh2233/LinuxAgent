"""进程、端口与服务工具。"""

from __future__ import annotations

import shlex
from typing import Literal

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool


@tool(
    name="process_list",
    description="列出当前 CPU 或内存占用最高的前 N 个进程（默认前 10）。",
    risk_hint=RiskLevel.LOW,
)
def process_list(
    ctx: ToolContext,
    sort_by: Literal["cpu", "mem"] = "cpu",
    top_n: int = 10,
) -> ToolResult:
    top_n = min(max(top_n, 1), 50)
    sort_key = "%cpu" if sort_by == "cpu" else "%mem"
    cmd = f"ps aux --sort=-{sort_key} | head -n {top_n + 1}"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="process_list")


@tool(
    name="port_check",
    description="查询指定 TCP 端口当前的监听进程。",
    risk_hint=RiskLevel.LOW,
)
def port_check(ctx: ToolContext, port: int) -> ToolResult:
    if not (1 <= port <= 65535):
        return ToolResult.from_error("port_check", f"Invalid port: {port}")
    cmd = (
        f"ss -tlnp 2>/dev/null | grep -E ':{port}\\b' || "
        f"netstat -tlnp 2>/dev/null | grep -E ':{port}\\b' || "
        f"echo 'port {port} is not in LISTEN state'"
    )
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="port_check")


@tool(
    name="service_status",
    description="查询 systemd 服务的运行状态（systemctl status）。",
    risk_hint=RiskLevel.LOW,
)
def service_status(ctx: ToolContext, service: str) -> ToolResult:
    cmd = f"systemctl status {shlex.quote(service)} --no-pager --lines=10"
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="service_status")
