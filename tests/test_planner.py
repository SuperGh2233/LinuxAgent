"""tests/test_planner.py — Plan-Act 规划层单元测试（全 mock，无需 LLM）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from linuxagent.core.planner import Plan, PlanStep, make_plan


# ──────────────────── fixtures ────────────────────


def _make_llm(content: str) -> MagicMock:
    """构造一个返回固定 content 的 LLMGateway mock。"""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    llm.chat.return_value = resp
    return llm


# ──────────────────── make_plan 测试 ────────────────────


def test_make_plan_happy_path():
    """正常 JSON 响应 → 生成包含多步的 Plan。"""
    payload = {
        "goal": "检查磁盘使用情况",
        "steps": [
            {"title": "查看分区", "detail": "运行 df -h", "tool_hint": "run_command"},
            {"title": "找大目录", "detail": "运行 du -sh /*", "tool_hint": "run_command"},
        ],
    }
    llm = _make_llm(json.dumps(payload, ensure_ascii=False))
    plan = make_plan(llm, "检查磁盘使用情况")

    assert plan.goal == "检查磁盘使用情况"
    assert len(plan.steps) == 2
    assert plan.steps[0].title == "查看分区"
    assert plan.steps[0].tool_hint == "run_command"
    assert all(s.status == "pending" for s in plan.steps)


def test_make_plan_invalid_json_fallback():
    """LLM 返回无效 JSON → 降级为单步兜底计划，不抛出异常。"""
    llm = _make_llm("这不是 JSON！{{{")
    plan = make_plan(llm, "修复服务器崩溃")

    assert plan.goal == "修复服务器崩溃"
    assert len(plan.steps) == 1
    assert plan.steps[0].status == "pending"


# ──────────────────── Plan.advance / complete_current 测试 ────────────────────


def test_plan_advance_and_complete():
    """advance 推进步骤到 running，complete_current 标记为 done 并推进指针。"""
    plan = Plan(
        goal="测试目标",
        steps=[
            PlanStep(title="第一步", tool_hint="run_command"),
            PlanStep(title="第二步", tool_hint="read_file"),
        ],
    )

    # advance：第一个 pending 步骤变为 running
    advanced = plan.advance("run_command")
    assert advanced is True
    assert plan.steps[0].status == "running"
    assert plan.steps[1].status == "pending"

    # complete_current：第一步 done，指针推进
    plan.complete_current("df -h 输出正常")
    assert plan.steps[0].status == "done"
    assert "df -h" in plan.steps[0].detail

    # advance 第二步
    plan.advance("read_file")
    assert plan.steps[1].status == "running"


def test_plan_render_markdown_contains_icons():
    """render_markdown 输出包含正确的状态图标。"""
    plan = Plan(
        goal="渲染测试",
        steps=[
            PlanStep(title="步骤A", status="done"),
            PlanStep(title="步骤B", status="running"),
            PlanStep(title="步骤C", status="pending"),
            PlanStep(title="步骤D", status="skipped"),
        ],
    )
    md = plan.render_markdown()

    assert "✅" in md   # done
    assert "🔄" in md   # running
    assert "⬜" in md   # pending
    assert "⏭️" in md  # skipped
    assert "渲染测试" in md
    assert "步骤A" in md
