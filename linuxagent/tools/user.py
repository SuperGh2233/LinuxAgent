"""用户管理工具。"""

from __future__ import annotations

import re
import shlex

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@tool(
    name="user_list",
    description="列出系统中的普通用户（UID >= 1000 且非 nobody），返回用户名、UID、家目录、默认 shell。",
    risk_hint=RiskLevel.LOW,
)
def user_list(ctx: ToolContext) -> ToolResult:
    cmd = (
        "getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 "
        "{ printf \"%-20s uid=%-6s home=%-25s shell=%s\\n\", $1, $3, $6, $7 }'"
    )
    return ToolResult.from_exec(ctx.executor.run(cmd), tool_name="user_list")


@tool(
    name="user_create",
    description=(
        "创建一个新的普通用户。默认创建家目录，默认 shell 为 /bin/bash。"
        "用户名必须以小写字母或下划线开头，仅允许小写字母、数字、下划线、短横。"
    ),
    risk_hint=RiskLevel.MEDIUM,
)
def user_create(
    ctx: ToolContext,
    username: str,
    shell: str = "/bin/bash",
    create_home: bool = True,
) -> ToolResult:
    if not _USERNAME_RE.match(username):
        return ToolResult.from_error("user_create", f"Invalid username: {username!r}")
    parts = ["useradd"]
    if create_home:
        parts.append("-m")
    parts.extend(["-s", shlex.quote(shell), shlex.quote(username)])
    cmd = " ".join(parts)
    return ToolResult.from_exec(ctx.executor.run(cmd, sudo=True), tool_name="user_create")


@tool(
    name="user_delete",
    description=(
        "删除指定用户。remove_home=True 会同时移除家目录和邮件池（不可恢复，高危）。"
        "禁止删除 root。"
    ),
    risk_hint=RiskLevel.HIGH,
)
def user_delete(
    ctx: ToolContext,
    username: str,
    remove_home: bool = False,
) -> ToolResult:
    if not username or username == "root":
        return ToolResult.from_error("user_delete", f"Refuse to delete user: {username!r}")
    if not _USERNAME_RE.match(username):
        return ToolResult.from_error("user_delete", f"Invalid username: {username!r}")
    parts = ["userdel"]
    if remove_home:
        parts.append("-r")
    parts.append(shlex.quote(username))
    cmd = " ".join(parts)
    return ToolResult.from_exec(ctx.executor.run(cmd, sudo=True), tool_name="user_delete")
