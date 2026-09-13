"""三闸风险评估引擎。

闸门：
1. 规则层：正则匹配 ``rules.yaml``，命中 BLOCKED 立即短路
2. 静态启发：工具 ``risk_hint`` + 参数分析（敏感路径 / 递归标志 / 通配符等）
3. LLM 语义评估：可选，5 秒超时失败降级

最终 ``level = max(rule, static, semantic)``。BLOCKED 只能由规则层给出。
``requires_confirmation = (final == HIGH)``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from linuxagent.core.llm import LLMGateway
from linuxagent.core.prompts import RISK_SEMANTIC_SYSTEM_PROMPT
from linuxagent.tools.base import RiskLevel, risk_max
from linuxagent.utils.errors import ConfigurationError

_SENSITIVE_PATHS: tuple[str, ...] = (
    "/",
    "/etc",
    "/boot",
    "/usr",
    "/var",
    "/var/lib",
    "/root",
    "/home",
    "/lib",
    "/bin",
    "/sbin",
)


class RiskRule(BaseModel):
    """YAML 规则单元。"""

    id: str
    description: str = ""
    pattern: str
    level: RiskLevel
    reason: str = ""
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v: Any) -> RiskLevel:
        return RiskLevel(v) if isinstance(v, str) else v


class RiskAssessment(BaseModel):
    """单次评估结果。"""

    level: RiskLevel
    reason: str = ""
    rules: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    source: str = "combined"


class RiskEngine:
    """三闸风险引擎。"""

    def __init__(
        self,
        rules: list[RiskRule],
        llm: LLMGateway | None = None,
        enable_semantic: bool = True,
        semantic_timeout_s: float = 5.0,
    ) -> None:
        self.rules = rules
        self._compiled: list[tuple[RiskRule, re.Pattern[str]]] = []
        for r in rules:
            try:
                self._compiled.append((r, re.compile(r.pattern, re.IGNORECASE)))
            except re.error as e:
                logger.error("invalid rule pattern id={} err={}", r.id, e)
        self.llm = llm if enable_semantic else None
        self.enable_semantic = enable_semantic and (llm is not None)
        self.semantic_timeout_s = semantic_timeout_s
        logger.info(
            "RiskEngine ready: rules={} semantic={}",
            len(self._compiled),
            self.enable_semantic,
        )

    def assess(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        rendered_cmd: str,
        tool_hint: RiskLevel = RiskLevel.LOW,
    ) -> RiskAssessment:
        """对一次即将发生的工具调用做三闸评估。

        Args:
            tool_name: 工具注册名。
            arguments: 工具参数字典。
            rendered_cmd: 命令的人类可读预览（``render_command_preview`` 产出）。
            tool_hint: 工具本身的风险提示。

        Returns:
            RiskAssessment 最终结果。
        """
        rule_hits = [r for r, pat in self._compiled if pat.search(rendered_cmd)]
        rule_level = risk_max(*(h.level for h in rule_hits)) if rule_hits else RiskLevel.LOW

        if rule_level == RiskLevel.BLOCKED:
            blocked_ids = [h.id for h in rule_hits if h.level == RiskLevel.BLOCKED]
            return RiskAssessment(
                level=RiskLevel.BLOCKED,
                reason=f"命中 BLOCKED 规则：{', '.join(blocked_ids)}",
                rules=[h.id for h in rule_hits],
                suggestions=_merge_suggestions(rule_hits),
                requires_confirmation=False,
                source="rule",
            )

        static_level, static_reasons = self._static_analyze(tool_name, arguments, tool_hint)

        semantic_level = RiskLevel.LOW
        semantic_reason = ""
        # 只读工具不会产生破坏性后果，跳过 LLM 语义层，避免误判
        is_readonly = tool_name in self._READONLY_TOOLS
        if self.enable_semantic and not is_readonly:
            try:
                semantic_level, semantic_reason = self._semantic_review(rendered_cmd)
            except Exception as e:
                logger.warning("semantic review failed, degraded LOW: {}", e)

        final_level = risk_max(rule_level, static_level, semantic_level)

        reason_parts: list[str] = []
        if rule_hits:
            reason_parts.append("规则: " + ", ".join(h.id for h in rule_hits))
        if static_reasons:
            reason_parts.append("静态: " + "; ".join(static_reasons))
        if semantic_reason:
            reason_parts.append("语义: " + semantic_reason)
        reason = "; ".join(reason_parts) or f"工具默认提示：{tool_hint.value}"

        return RiskAssessment(
            level=final_level,
            reason=reason,
            rules=[h.id for h in rule_hits],
            suggestions=_merge_suggestions(rule_hits),
            requires_confirmation=(final_level == RiskLevel.HIGH),
            source="combined",
        )

    # 只读工具：路径在敏感目录不升级为 HIGH
    _READONLY_TOOLS: frozenset[str] = frozenset({
        "disk_usage", "disk_inode",
        "file_stat", "file_read_tail", "file_search",
        "system_info", "system_load",
        "process_list", "port_check", "service_status",
        "user_list",
        "network_ping", "network_route", "network_interfaces",
    })

    # run_command 中被视为只读的 shell 命令前缀（无副作用，不升级风险）
    _READONLY_SHELL_CMDS: frozenset[str] = frozenset({
        # 文件系统查看
        "ls", "ll", "la", "lsblk", "lsof", "lscpu", "lsmem",
        "df", "du", "free", "top", "htop", "uptime",
        "cat", "head", "tail", "less", "more",
        "stat", "file", "find", "locate",
        # 磁盘信息查询（只读模式）
        "fdisk -l", "fdisk --list",
        "parted -l", "parted --list", "parted print",
        "blkid", "lsscsi", "smartctl -i", "smartctl -a", "smartctl --info",
        "hdparm -i", "hdparm --identify",
        "nvme list", "nvme id-ctrl", "nvme smart-log",
        # 进程 / 系统状态
        "ps", "pstree", "pgrep",
        "uname", "hostname", "id", "whoami", "who", "w",
        # 网络查询
        "ip", "ifconfig", "ss", "netstat", "route",
        "ping", "traceroute", "dig", "nslookup",
        # 服务状态
        "systemctl status", "systemctl list",
        "journalctl",
        # 环境 / 包信息
        "env", "printenv", "echo", "pwd", "date", "cal",
        "dpkg -l", "rpm -q", "rpm -qa", "apt list", "yum list", "dnf list",
        "pip list", "pip show",
        # 内核 / 硬件信息
        "dmesg", "dmidecode", "lshw", "lspci", "lsusb",
    })

    def _is_readonly_shell_cmd(self, cmd: str) -> bool:
        """判断 run_command 的 cmd 是否为只读/无副作用的 shell 命令。"""
        cmd_stripped = cmd.strip()
        if cmd_stripped.startswith("sudo "):
            cmd_stripped = cmd_stripped[5:].strip()
        for prefix in self._READONLY_SHELL_CMDS:
            if cmd_stripped == prefix or cmd_stripped.startswith(prefix + " "):
                return True
        return False

    def _static_analyze(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_hint: RiskLevel,
    ) -> tuple[RiskLevel, list[str]]:
        reasons: list[str] = []

        # run_command 特殊处理：若 cmd 为只读命令，基础风险降为 LOW
        effective_hint = tool_hint
        if tool_name == "run_command":
            cmd_val = arguments.get("cmd", "")
            if isinstance(cmd_val, str) and self._is_readonly_shell_cmd(cmd_val):
                effective_hint = RiskLevel.LOW

        level = effective_hint
        is_readonly = tool_name in self._READONLY_TOOLS

        for key, value in arguments.items():
            if isinstance(value, str):
                if value in _SENSITIVE_PATHS and not is_readonly:
                    # 只读工具访问敏感路径是正常操作，不升级
                    level = risk_max(level, RiskLevel.HIGH)
                    reasons.append(f"参数 {key}={value!r} 为系统敏感路径")
                if ("*" in value or "?" in value) and not is_readonly:
                    # 只读工具的 pattern 参数含通配符是正常用法，不升级
                    level = risk_max(level, RiskLevel.MEDIUM)
                    reasons.append(f"参数 {key} 含通配符")
            if isinstance(value, bool) and key.startswith("remove") and value:
                level = risk_max(level, RiskLevel.HIGH)
                reasons.append(f"{key}=True 是破坏性选项")
            if key == "recursive" and value and not is_readonly:
                # 只读工具的递归参数（如递归搜索）不升级
                level = risk_max(level, RiskLevel.MEDIUM)
                reasons.append("启用了递归标志")

        return level, reasons

    def _semantic_review(self, rendered_cmd: str) -> tuple[RiskLevel, str]:
        assert self.llm is not None
        messages = [
            {"role": "system", "content": RISK_SEMANTIC_SYSTEM_PROMPT},
            {"role": "user", "content": f"即将执行：{rendered_cmd}"},
        ]
        resp = self.llm.chat(
            messages,
            tools=None,
            response_format={"type": "json_object"},
        )
        content = (resp.content or "").strip()
        if not content:
            return RiskLevel.MEDIUM, "LLM 返回空，保守降级 MEDIUM"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("semantic review non-json, fallback MEDIUM: {}", content[:200])
            return RiskLevel.MEDIUM, "LLM 返回非 JSON，保守降级"
        lv_raw = str(data.get("level", "MEDIUM")).upper()
        if lv_raw not in ("LOW", "MEDIUM", "HIGH"):
            lv_raw = "MEDIUM"
        reason = str(data.get("reason", ""))
        return RiskLevel(lv_raw), reason


def _merge_suggestions(rules: list[RiskRule]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for r in rules:
        for s in r.suggestions:
            if s not in seen:
                seen.add(s)
                merged.append(s)
    return merged


def load_rules(path: str | Path) -> list[RiskRule]:
    """从 YAML 加载规则列表。"""
    p = Path(path)
    if not p.exists():
        raise ConfigurationError(f"safety rules file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules_raw = data.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigurationError(f"'rules' must be a list in {p}")
    rules: list[RiskRule] = []
    for raw in rules_raw:
        try:
            rules.append(RiskRule(**raw))
        except Exception as e:
            raise ConfigurationError(
                f"invalid rule {raw.get('id', '?')} in {p}: {e}"
            ) from e
    if len(rules) < 12:
        logger.warning("rules count {} < 12, some scenes might be unguarded", len(rules))
    return rules


def build_risk_engine(
    settings: Any,
    llm: LLMGateway | None = None,
) -> RiskEngine:
    """按 Settings 构造 RiskEngine。"""
    rules = load_rules(settings.safety.rules_path)
    return RiskEngine(
        rules=rules,
        llm=llm if settings.safety.enable_semantic_review else None,
        enable_semantic=settings.safety.enable_semantic_review,
        semantic_timeout_s=settings.safety.semantic_timeout_s,
    )
