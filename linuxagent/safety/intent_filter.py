"""用户自然语言意图拦截层。

在 Agent 开始规划/执行之前，对用户的原始消息做关键词/正则匹配，
对明确的破坏性磁盘操作意图直接返回 BLOCKED，无需进入 LLM 推理循环。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class IntentRule:
    id: str
    description: str
    patterns: list[str]          # 正则列表，任意一条命中即触发
    reason: str
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def match(self, text: str) -> bool:
        return any(pat.search(text) for pat in self._compiled)


@dataclass
class IntentBlock:
    rule_id: str
    description: str
    reason: str
    suggestions: list[str]


# ── 意图级 BLOCKED 规则表 ─────────────────────────────────────────
_INTENT_RULES: list[IntentRule] = [
    IntentRule(
        id="intent.format_disk",
        description="禁止格式化磁盘操作",
        patterns=[
            # 中文：格式化 + 磁盘相关词
            r"格式化\s*(/dev/|磁盘|硬盘|分区|sda|nvme|vda|xvda)",
            r"(磁盘|硬盘|分区|sda|nvme)\s*(格式化|清空|清除|初始化)",
            # 英文：format + disk
            r"\bformat\s+(/dev/|disk|drive|partition|sda|nvme)",
            r"\b(mkfs|wipefs|sgdisk|parted\s+mklabel|fdisk)\b",
        ],
        reason="格式化磁盘会清空所有数据，不可逆，属于系统级破坏性操作",
        suggestions=[
            "此操作已被安全系统直接拦截，无需确认",
            "如确实需要格式化，请通过云平台控制台或带外管理执行",
            "操作前务必备份所有数据",
        ],
    ),
    IntentRule(
        id="intent.wipe_disk",
        description="禁止磁盘擦除/清零操作",
        patterns=[
            r"(磁盘|硬盘|sda|nvme|vda)\s*(擦除|清零|清盘|wipe|zero)",
            r"\b(dd\s+if=/dev/zero|shred)\b.*(/dev/sd|/dev/nvme|/dev/vd)",
            r"wipe\s+(disk|drive|/dev/)",
        ],
        reason="磁盘擦除/清零会永久销毁所有数据",
        suggestions=[
            "此操作已被安全系统直接拦截",
            "如需安全销毁数据，请通过专业数据销毁工具并获得授权",
        ],
    ),
    IntentRule(
        id="intent.destroy_system",
        description="禁止破坏系统根目录/关键目录",
        patterns=[
            r"(删除|清空|清除|destroy|remove)\s*(根目录|/\s|/etc|/boot|/usr|/var/lib)",
            r"rm\s+(-rf?|-fr?)\s+(/\s|/etc|/boot|/usr\b)",
        ],
        reason="删除系统关键目录会导致服务器不可恢复",
        suggestions=[
            "此操作已被安全系统直接拦截",
            "系统目录不可删除，如需重装请通过云平台控制台操作",
        ],
    ),
]


def check_intent(user_message: str) -> IntentBlock | None:
    """检查用户消息是否命中意图级 BLOCKED 规则。

    Returns:
        IntentBlock if blocked, None if allowed.
    """
    for rule in _INTENT_RULES:
        if rule.match(user_message):
            return IntentBlock(
                rule_id=rule.id,
                description=rule.description,
                reason=rule.reason,
                suggestions=rule.suggestions,
            )
    return None
