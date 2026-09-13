"""通用 Shell 命令执行工具。

此工具允许 Agent 在目标服务器上执行任意 Shell 命令。
由于执行范围宽泛，所有调用默认标注为 HIGH 风险，
并由 RiskEngine 规则层对危险命令（rm -rf、mkfs 等）执行 BLOCKED 拦截。
"""

from __future__ import annotations

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool

_MAX_CMD_LEN = 2048


@tool(
    name="run_command",
    description=(
        "在目标 Linux 服务器上执行任意 Shell 命令并返回 stdout/stderr。"
        "仅在没有更专用工具可用时使用此工具。"
        "高危命令（如 rm -rf 系统目录、mkfs 等）会被安全引擎自动拦截。"
        "命令长度上限 2048 字符。"
    ),
    risk_hint=RiskLevel.HIGH,
)
def run_command(
    ctx: ToolContext,
    cmd: str,
    sudo: bool = False,
) -> ToolResult:
    if not cmd or not cmd.strip():
        return ToolResult.from_error("run_command", "cmd 不能为空")
    if len(cmd) > _MAX_CMD_LEN:
        return ToolResult.from_error(
            "run_command",
            f"命令过长（{len(cmd)} > {_MAX_CMD_LEN}），请拆分执行",
        )
    return ToolResult.from_exec(
        ctx.executor.run(cmd, sudo=sudo),
        tool_name="run_command",
    )
