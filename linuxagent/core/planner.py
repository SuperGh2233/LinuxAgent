"""Plan-Act 规划层：把用户请求分解为可视化的执行步骤。

Planner 负责：
1. 在 ReAct 循环开始前，用 LLM 将用户请求拆解成 1-5 个步骤；
2. 将步骤状态（pending/running/done/skipped）同步给 UI；
3. 提供 ``render_markdown()`` 以在 Gradio 侧边栏中实时渲染进度看板。
"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field


PLANNER_SYSTEM_PROMPT = """你是一名资深 Linux 运维规划师。
用户会给你一个运维目标，你需要将其分解为 1-5 个顺序执行的步骤。

**输出要求（必须严格遵守）**：
- 仅输出 JSON 对象，不加任何额外说明。
- 结构如下：
{
  "goal": "一句话总结目标",
  "steps": [
    {
      "title": "步骤简短标题（≤15字）",
      "detail": "该步骤的具体操作说明",
      "tool_hint": "预计调用的工具名，如 run_command / read_file / write_file 等，若不确定留空"
    }
  ]
}
- steps 数量：1-5 个，过于简单的任务用 1-2 步即可。
- 标题简洁，避免冗余，面向运维场景。"""


StepStatus = Literal["pending", "running", "done", "skipped"]


class PlanStep(BaseModel):
    """单个规划步骤。"""

    title: str
    detail: str = ""
    tool_hint: str = ""
    status: StepStatus = "pending"


class Plan(BaseModel):
    """完整执行计划。"""

    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    _current_idx: int = 0

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "_current_idx", 0)

    # ──────────────────── 状态查询 ────────────────────

    @property
    def current_idx(self) -> int:
        return object.__getattribute__(self, "_current_idx")

    def next_pending(self) -> PlanStep | None:
        """返回第一个 pending 步骤，若无则 None。"""
        for step in self.steps:
            if step.status == "pending":
                return step
        return None

    def is_finished(self) -> bool:
        """计划是否已完成（没有 pending/running 步骤）。"""
        if not self.steps:
            return False
        return all(s.status in ("done", "skipped") for s in self.steps)

    # ──────────────────── 状态推进 ────────────────────

    def advance(self, tool_name: str) -> bool:
        """根据工具调用名推进当前步骤到 running 状态。

        启发式逻辑：
        - 找到第一个 pending 步骤，判断 tool_hint 是否匹配或为空；
        - 若匹配（或无 hint），将其标记为 running 并更新 _current_idx；
        - 返回 True 表示推进了，False 表示未推进。
        """
        idx = self.current_idx
        # 从当前位置起找第一个 pending
        for i in range(idx, len(self.steps)):
            step = self.steps[i]
            if step.status == "pending":
                hint = step.tool_hint.strip().lower()
                tname = tool_name.strip().lower()
                if not hint or hint in tname or tname in hint:
                    step.status = "running"
                    object.__setattr__(self, "_current_idx", i)
                    return True
                # hint 不匹配时也推进（顺序执行兜底）
                step.status = "running"
                object.__setattr__(self, "_current_idx", i)
                return True
        return False

    def complete_current(self, brief: str = "") -> None:
        """将当前 running 步骤标记为 done，detail 追加执行摘要。"""
        idx = self.current_idx
        if idx < len(self.steps):
            step = self.steps[idx]
            if step.status == "running":
                step.status = "done"
                if brief:
                    step.detail = brief[:200]
                # 推进指针到下一个
                object.__setattr__(self, "_current_idx", idx + 1)

    def mark_remaining_skipped(self) -> None:
        """将所有剩余 pending/running 步骤标记为 skipped（LLM 提前结束时用）。"""
        for step in self.steps:
            if step.status in ("pending", "running"):
                step.status = "skipped"

    # ──────────────────── UI 渲染 ────────────────────

    def render_markdown(self) -> str:
        """生成侧边栏用的 Markdown 进度看板。"""
        _icon: dict[StepStatus, str] = {
            "pending": "⬜",
            "running": "🔄",
            "done": "✅",
            "skipped": "⏭️",
        }
        lines: list[str] = [f"### 🗺️ 执行计划\n**目标**：{self.goal}\n"]
        for i, step in enumerate(self.steps, 1):
            icon = _icon.get(step.status, "⬜")
            status_label = {
                "pending": "待执行",
                "running": "**执行中**",
                "done": "已完成",
                "skipped": "已跳过",
            }.get(step.status, "")
            lines.append(f"{i}. {icon} **{step.title}** — {status_label}")
            if step.status == "running" and step.detail:
                lines.append(f"   > {step.detail}")
            elif step.status == "done" and step.detail:
                lines.append(f"   > ✔ {step.detail}")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 工厂函数
# ──────────────────────────────────────────────────────────────

def make_plan(llm: Any, user_message: str) -> Plan:
    """调用 LLM（JSON 模式）生成 Plan 对象。

    失败时（网络、JSON 解析等）返回仅含一个 fallback 步骤的计划，
    确保主流程不会因规划失败而中断。

    Args:
        llm: ``LLMGateway`` 实例（同步 ``chat`` 方法）。
        user_message: 用户的原始请求。

    Returns:
        生成的 ``Plan`` 实例。
    """
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    try:
        resp = llm.chat(
            messages,
            tools=None,
            tool_choice="none",
            response_format={"type": "json_object"},
        )
        raw_content = (resp.content or "").strip()
        data = json.loads(raw_content)
        goal = data.get("goal", user_message[:80])
        raw_steps = data.get("steps", [])
        steps: list[PlanStep] = []
        for s in raw_steps[:5]:
            if isinstance(s, dict):
                steps.append(
                    PlanStep(
                        title=str(s.get("title", "步骤"))[:30],
                        detail=str(s.get("detail", "")),
                        tool_hint=str(s.get("tool_hint", "")),
                    )
                )
        if not steps:
            steps = [PlanStep(title="执行任务", detail=user_message[:100])]
        plan = Plan(goal=goal, steps=steps)
        logger.info("Planner generated {} steps for: {!r}", len(steps), goal)
        return plan
    except Exception as e:
        logger.warning("Planner failed ({}), using fallback single-step plan", e)
        return Plan(
            goal=user_message[:80],
            steps=[PlanStep(title="执行任务", detail=user_message[:100], tool_hint="")],
        )
