"""通用小工具函数。"""

from __future__ import annotations

from typing import Any


def safe_truncate(
    text: str | None,
    max_chars: int = 2048,
    placeholder: str = "\n...[truncated]...\n",
) -> tuple[str, bool]:
    """按首尾对半保留方式截断字符串。

    Args:
        text: 原文本。
        max_chars: 目标最大字符数。
        placeholder: 中间填充字符串。

    Returns:
        元组 ``(截断后文本, 是否发生了截断)``。
    """
    if text is None:
        return "", False
    if len(text) <= max_chars:
        return text, False
    half = max(1, (max_chars - len(placeholder)) // 2)
    return text[:half] + placeholder + text[-half:], True


def format_bytes(num: int | float) -> str:
    """把字节数格式化为人类可读字符串。"""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}EB"


_SHELL_TEMPLATES: dict[str, str] = {
    "disk_usage": "df -h {path}",
    "disk_inode": "df -i {path}",
    "file_search": "find {path} -name {pattern}",
    "file_read_tail": "tail -n {lines} {path}",
    "file_stat": "stat {path}",
    "process_list": "ps aux --sort=-%{sort_by}",
    "port_check": "ss -tlnp | grep :{port}",
    "service_status": "systemctl status {service}",
    "user_list": "getent passwd",
    "user_create": "useradd {username}",
    "network_ping": "ping -c {count} {target}",
    "network_route": "ip route",
    "network_interfaces": "ip addr",
    "system_info": "uname -a",
    "system_load": "uptime",
    # run_command 直接透传 cmd，让规则层能匹配真实命令（如 rm -rf /etc）
    "run_command": "{cmd}",
}


def render_command_preview(tool_name: str, arguments: dict[str, Any]) -> str:
    """把工具名 + 参数渲染成一条人类可读的命令预览。

    主要服务于 RiskEngine 的正则匹配，不要求完全等价于真实命令，
    只求覆盖高危片段以便规则层能识别到。

    Args:
        tool_name: 工具注册名。
        arguments: 工具参数字典。

    Returns:
        形如 ``tool_name(...) :: df -h /`` 的字符串。
    """
    args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    base = f"{tool_name}({args_str})"

    if tool_name == "user_delete":
        remove_home = bool(arguments.get("remove_home", False))
        username = arguments.get("username", "")
        shell = f"userdel {'-r ' if remove_home else ''}{username}".strip()
        return f"{base} :: {shell}"

    template = _SHELL_TEMPLATES.get(tool_name)
    if not template:
        return base
    try:
        return f"{base} :: {template.format(**arguments)}"
    except Exception:
        return f"{base} :: {template}"
