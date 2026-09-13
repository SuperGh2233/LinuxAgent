"""LinuxAgent 业务异常体系。

所有业务异常统一继承自 LinuxAgentError，方便 UI 层做统一兜底。
"""

from __future__ import annotations


class LinuxAgentError(Exception):
    """LinuxAgent 业务异常基类。"""


class ConfigurationError(LinuxAgentError):
    """配置解析或校验失败。"""


class SSHConnectionError(LinuxAgentError):
    """SSH 连接建立或保持失败。"""


class SSHExecutionError(LinuxAgentError):
    """SSH 命令执行过程异常（channel / timeout / decode）。"""


class ToolInvocationError(LinuxAgentError):
    """工具调用失败（参数校验、找不到工具、执行异常）。"""


class LLMCallError(LinuxAgentError):
    """LLM 调用失败（网络、解析、重试耗尽）。"""


class SafetyBlockedError(LinuxAgentError):
    """命令被风险引擎直接拦截（BLOCKED 级别）。"""
