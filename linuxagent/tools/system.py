"""系统探测工具。"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from linuxagent.tools.base import RiskLevel, ToolContext, ToolResult, tool


@tool(
    name="system_info",
    description="查询目标服务器的系统信息（内核、发行版、架构），用于环境感知。",
    risk_hint=RiskLevel.LOW,
)
def system_info(ctx: ToolContext) -> ToolResult:
    exec_result = ctx.executor.run("uname -a && cat /etc/os-release 2>/dev/null || true")
    return ToolResult.from_exec(exec_result, tool_name="system_info")


@tool(
    name="system_load",
    description="查询系统负载与运行时长（uptime 与 /proc/loadavg）。",
    risk_hint=RiskLevel.LOW,
)
def system_load(ctx: ToolContext) -> ToolResult:
    exec_result = ctx.executor.run("uptime && cat /proc/loadavg 2>/dev/null")
    return ToolResult.from_exec(exec_result, tool_name="system_load")


def probe_env(executor: Any) -> dict[str, Any]:
    """探测目标服务器的环境快照。

    返回字典包含 ``os_name / os_version / kernel / arch / pkg_mgr / has_systemd``。
    任何单项失败都会退化为默认值，不抛异常。
    """
    snapshot: dict[str, Any] = {
        "os_name": "unknown",
        "os_version": "",
        "kernel": "",
        "arch": "",
        "pkg_mgr": "",
        "has_systemd": False,
    }
    try:
        kern = executor.run("uname -r")
        if kern.success:
            snapshot["kernel"] = kern.stdout.strip()

        arch = executor.run("uname -m")
        if arch.success:
            snapshot["arch"] = arch.stdout.strip()

        osr = executor.run("cat /etc/os-release 2>/dev/null || true")
        if osr.success and osr.stdout:
            m_name = re.search(r'^NAME="?([^"\n]+)"?', osr.stdout, re.MULTILINE)
            m_ver = re.search(r'^VERSION="?([^"\n]+)"?', osr.stdout, re.MULTILINE)
            if m_name:
                snapshot["os_name"] = m_name.group(1).strip()
            if m_ver:
                snapshot["os_version"] = m_ver.group(1).strip()

        for candidate in ("apt", "dnf", "yum", "zypper", "pacman"):
            result = executor.run(f"command -v {candidate}")
            if result.success and result.stdout.strip():
                snapshot["pkg_mgr"] = candidate
                break

        systemctl = executor.run("command -v systemctl")
        snapshot["has_systemd"] = bool(systemctl.success and systemctl.stdout.strip())
    except Exception as e:
        logger.warning("probe_env failed: {}", e)
    return snapshot
