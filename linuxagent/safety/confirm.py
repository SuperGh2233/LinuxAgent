"""确认协议：UI 层与 Agent 之间的数据契约。

MVP 只提供数据结构与 Markdown 格式化；真正的交互由 UI 层实现。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from linuxagent.core.prompts import CONFIRMATION_EXPLAIN_TEMPLATE
from linuxagent.safety.risk_engine import RiskAssessment


class ConfirmationPrompt(BaseModel):
    """向用户展示的风险确认卡片。"""

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    rendered_cmd: str
    assessment: RiskAssessment

    def to_markdown(self) -> str:
        """渲染为带风险解释、参数和命令预览的 Markdown。"""
        a = self.assessment
        head = CONFIRMATION_EXPLAIN_TEMPLATE.format(
            level=a.level.value,
            reason=a.reason,
            rules="、".join(a.rules) or "（无具体规则命中）",
            suggestions="；".join(a.suggestions) or "（无）",
        )
        args_json = json.dumps(self.arguments, ensure_ascii=False, indent=2)
        body = (
            f"\n\n**工具**：`{self.tool_name}`\n\n"
            f"**参数**：\n\n```json\n{args_json}\n```\n\n"
            f"**近似命令**：\n\n```bash\n{self.rendered_cmd}\n```\n"
        )
        return head + body


def level_emoji(level: str) -> str:
    """把风险等级映射为前端表意的色块 emoji。"""
    mapping = {
        "BLOCKED": "🟥",
        "HIGH": "🟧",
        "MEDIUM": "🟨",
        "LOW": "🟩",
    }
    return mapping.get(level.upper(), "⬜")
