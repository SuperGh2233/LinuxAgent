"""Gradio Web UI 组件与事件处理器。

本模块提供独立的 Gradio 组件工厂函数，供 ``app_web.py`` 组装。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from linuxagent.core.agent import Agent, AgentEvent
from linuxagent.safety.confirm import ConfirmationPrompt, level_emoji


_LEVEL_COLORS: dict[str, str] = {
    "BLOCKED": "#ff4444",
    "HIGH": "#ff8c00",
    "MEDIUM": "#f5a623",
    "LOW": "#27ae60",
}

_LEVEL_BG: dict[str, str] = {
    "BLOCKED": "#fff0f0",
    "HIGH": "#fff7f0",
    "MEDIUM": "#fffbf0",
    "LOW": "#f0fff4",
}


def format_risk_card(assessment: dict[str, Any]) -> str:
    """把 RiskAssessment dict 格式化为简洁的风险提示。
    BLOCKED 单独渲染；HIGH/MEDIUM 显示为引用块。
    """
    level = assessment.get("level", "LOW")
    emoji = level_emoji(level)
    reason = assessment.get("reason", "")
    suggestions = assessment.get("suggestions", [])

    if level == "BLOCKED":
        # BLOCKED 用红色背景块（tool_result 会再次渲染拦截卡，这里只做简短标注）
        return f"> 🚫 **安全引擎拦截** — {reason}"

    lines = [f"> {emoji} **风险等级：{level}**　{reason}"]
    for s in suggestions[:2]:
        lines.append(f">  💡 {s}")
    return "\n".join(lines)


def format_tool_call_badge(tool_name: str, arguments: dict[str, Any]) -> str:
    """把工具调用格式化为简洁的行内摘要。"""
    args_brief = ", ".join(f"{k}={v!r}" for k, v in list(arguments.items())[:2])
    if len(arguments) > 2:
        args_brief += ", ..."
    return f"`{tool_name}({args_brief})`"


def format_tool_result(result_payload: dict[str, Any]) -> str:
    """把 ToolResult 格式化为可折叠的 HTML details 块，避免原始输出污染对话。
    BLOCKED 操作单独渲染为醒目的拦截提示，不折叠。
    """
    tool_name = result_payload.get("tool_name", "?")
    success = result_payload.get("success", False)
    content = result_payload.get("content", "")
    error = result_payload.get("error", "")

    # BLOCKED 操作：直接显示拦截提示，不需要折叠
    if error == "blocked_by_safety":
        reason = content.replace("[BLOCKED] ", "").split(". 建议：")[0]
        suggestion_part = content.split("建议：")[-1] if "建议：" in content else ""
        blocked_html = (
            f'<div style="background:#fff0f0;border:2px solid #dc2626;border-radius:10px;'
            f'padding:12px 14px;margin:6px 0;">'
            f'<b>🚫 操作已被安全系统拦截</b><br>'
            f'<span style="color:#7f1d1d;font-size:0.92em;">{reason}</span>'
        )
        if suggestion_part and suggestion_part != "无":
            blocked_html += (
                f'<br><span style="color:#92400e;font-size:0.85em;">💡 建议：{suggestion_part}</span>'
            )
        blocked_html += "</div>"
        return blocked_html

    status_icon = "✅" if success else "❌"

    if not content:
        return f"> {status_icon} `{tool_name}` 执行完成（无输出）"

    short_content = content[:800]
    if len(content) > 800:
        short_content += "\n… [仅显示前 800 字符]"

    return (
        f"<details>\n"
        f"<summary>{status_icon} <code>{tool_name}</code> "
        f"{'执行成功' if success else '执行失败'} · 点击展开原始输出</summary>\n\n"
        f"```\n{short_content}\n```\n\n"
        f"</details>"
    )


def _msg(role: str, content: str) -> dict[str, str]:
    """Gradio 6.x 消息格式：字典 {role, content}。"""
    return {"role": role, "content": content}


async def run_agent_streaming(
    agent: Agent,
    user_message: str,
    history: list[dict[str, str]],
) -> Any:
    """异步生成器：把 Agent 事件流转换为 Gradio 6 Chatbot 可消费的增量输出。

    显示结构（由上到下）：
      [工具执行区] 折叠的 details 块，执行过程中实时追加
      [最终回复区] final 事件到达时替换整条消息，突出展示

    Yields:
        ``(updated_history, status_html, confirm_visible, confirm_md, plan_md)`` 元组。
    """
    new_history = list(history)
    new_history.append(_msg("user", user_message))
    new_history.append(_msg("assistant", ""))
    status = "🤔 思考中..."
    confirm_visible = False
    confirm_md = ""
    plan_md = ""

    # 工具执行记录（折叠块内容）和最终回复分开存储
    tool_log: list[str] = []   # 工具调用/结果/风险 等折叠块内容
    final_answer: str = ""     # LLM 最终回复

    def _render_message() -> str:
        """拼接当前消息：工具日志（折叠）+ 最终回复（醒目）。"""
        parts: list[str] = []
        if tool_log:
            log_body = "\n\n".join(tool_log)
            parts.append(
                f"<details>\n"
                f"<summary>🔩 执行详情（{len(tool_log)} 步）· 点击展开</summary>\n\n"
                f"{log_body}\n\n"
                f"</details>"
            )
        if final_answer:
            parts.append(f"\n{final_answer}")
        return "\n\n".join(parts) if parts else ""

    async for ev in agent.chat(user_message):
        if ev.type == "plan_created":
            from linuxagent.core.planner import Plan
            plan = Plan(**ev.payload["plan"])
            plan_md = plan.render_markdown()
            status = "📋 规划完成，开始执行..."

        elif ev.type == "plan_updated":
            from linuxagent.core.planner import Plan
            plan = Plan(**ev.payload["plan"])
            plan_md = plan.render_markdown()

        elif ev.type == "thinking":
            step = ev.payload.get("step", 0)
            status = f"🔄 ReAct 步骤 {step + 1}..."
            rendered = _render_message()
            new_history[-1] = _msg("assistant", rendered or f"*{status}*")

        elif ev.type == "tool_call":
            tc_name = ev.payload.get("name", "?")
            tc_args = ev.payload.get("arguments", {})
            badge = format_tool_call_badge(tc_name, tc_args)
            status = f"🔧 调用工具：{tc_name}"
            tool_log.append(f"🔧 **调用** {badge}")
            new_history[-1] = _msg("assistant", _render_message())

        elif ev.type == "tool_result":
            formatted = format_tool_result(ev.payload)
            # 替换最后一条 tool_log（把 badge 行升级为带输出的 details 块）
            if tool_log:
                tool_log[-1] = formatted
            else:
                tool_log.append(formatted)
            new_history[-1] = _msg("assistant", _render_message())
            status = "✅ 工具执行完成"

        elif ev.type == "risk_warning":
            level = ev.payload.get("level", "LOW")
            card = format_risk_card(ev.payload)
            tool_log.append(card)
            new_history[-1] = _msg("assistant", _render_message())
            status = f"⚠️ 风险：{level}"

        elif ev.type == "confirmation_required":
            confirm_visible = True
            assessment = ev.payload.get("assessment", {})
            tc_payload = ev.payload.get("tool_call", {})
            from linuxagent.utils.helpers import render_command_preview
            rendered_cmd = render_command_preview(
                tc_payload.get("name", "?"), tc_payload.get("arguments", {})
            )
            from linuxagent.safety.risk_engine import RiskAssessment
            cp = ConfirmationPrompt(
                tool_call_id=tc_payload.get("id", ""),
                tool_name=tc_payload.get("name", "?"),
                arguments=tc_payload.get("arguments", {}),
                rendered_cmd=rendered_cmd,
                assessment=RiskAssessment(**assessment),
            )
            confirm_md = cp.to_markdown()
            status = "⏸️ 等待用户确认..."
            tool_log.append("⏸️ **高风险操作，等待您确认后继续**")
            new_history[-1] = _msg("assistant", _render_message())
            yield new_history, status, confirm_visible, confirm_md, plan_md
            return

        elif ev.type == "intent_blocked":
            desc = ev.payload.get("description", "")
            reason = ev.payload.get("reason", "")
            suggestions = ev.payload.get("suggestions", [])
            blocked_html = (
                '<div style="background:#fff0f0;border:2px solid #dc2626;border-radius:10px;'
                'padding:14px 16px;margin:8px 0;">'
                '<b style="color:#dc2626;font-size:1.05em;">🚫 操作已被安全系统直接拦截</b><br>'
                f'<span style="color:#7f1d1d;">📌 {desc}</span><br>'
                f'<span style="color:#7f1d1d;font-size:0.92em;">原因：{reason}</span>'
            )
            if suggestions:
                blocked_html += (
                    '<br><span style="color:#92400e;font-size:0.88em;">💡 '
                    + " · ".join(suggestions[:2])
                    + "</span>"
                )
            blocked_html += "</div>"
            final_answer = blocked_html
            new_history[-1] = _msg("assistant", _render_message())
            status = "🔴 意图级安全阻断"
            yield new_history, status, False, "", plan_md
            return

        elif ev.type == "final":
            content = ev.payload.get("content", "")
            final_answer = content  # 最终回复独立，不混入 tool_log
            new_history[-1] = _msg("assistant", _render_message())
            status = "✅ 完成"

        elif ev.type == "error":
            reason = ev.payload.get("reason", "unknown error")
            final_answer = f"❌ **出错**：{reason}"
            new_history[-1] = _msg("assistant", _render_message())
            status = f"❌ 错误: {reason}"

        yield new_history, status, confirm_visible, confirm_md, plan_md
