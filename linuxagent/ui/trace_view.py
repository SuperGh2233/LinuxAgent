"""思考过程可视化：把 TraceEntry 列表渲染为 Markdown 时间线。

供 app_web.py 使用，无 Gradio 依赖，纯字符串渲染。
"""

from __future__ import annotations

import json
from typing import Any

from linuxagent.core.trace_store import TraceEntry, Turn


# 每种事件类型对应的颜色标签（Markdown 加粗 + emoji 区分）
_EVENT_HEADER: dict[str, str] = {
    "thinking":             "🤔 **思考**",
    "plan_created":         "📋 **生成计划**",
    "plan_updated":         "🔄 **计划更新**",
    "tool_call":            "🔧 **调用工具**",
    "tool_result":          "📤 **工具结果**",
    "risk_warning":         "⚠️ **风险警告**",
    "confirmation_required":"⏸️ **等待确认**",
    "final":                "🏁 **最终回复**",
    "error":                "💥 **错误**",
}


def render_entry(entry: TraceEntry, show_payload: bool = False) -> str:
    """将单条 TraceEntry 渲染为 Markdown 时间线条目。

    Args:
        entry: 要渲染的条目。
        show_payload: 是否展开完整 payload（JSON 折叠块）。
    """
    ts_str = entry.ts.strftime("%H:%M:%S.%f")[:-3]
    header = _EVENT_HEADER.get(entry.event_type, f"· **{entry.event_type}**")
    lines = [f"**`{ts_str}`** &nbsp; {header}"]
    lines.append(f"> {entry.summary}")

    if show_payload and entry.payload:
        payload_str = json.dumps(entry.payload, ensure_ascii=False, indent=2)
        # 截断超长 payload
        if len(payload_str) > 2000:
            payload_str = payload_str[:2000] + "\n... [截断] ..."
        lines.append(f"\n```json\n{payload_str}\n```")

    return "\n".join(lines)


def render_turn_timeline(
    turn: Turn,
    show_payload: bool = False,
    show_plan_steps: bool = True,
) -> str:
    """将整个 Turn 渲染为完整 Markdown 时间线。

    Args:
        turn: 要渲染的 Turn。
        show_payload: 是否为每条 entry 展开 JSON payload。
        show_plan_steps: 是否在 plan_created/plan_updated 时内联渲染步骤进度。
    """
    if not turn.entries:
        return "*（本轮暂无记录）*"

    dur = f" · 耗时 {turn.duration_s:.1f}s" if turn.duration_s else ""
    lines: list[str] = [
        f"### 🗂️ Turn {turn.turn_id} 思考过程{dur}",
        f"**用户输入**：{turn.user_message}",
        "",
        "---",
        "",
    ]

    for entry in turn.entries:
        lines.append(render_entry(entry, show_payload=show_payload))

        # 内联展示计划进度条
        if show_plan_steps and entry.event_type in ("plan_created", "plan_updated"):
            steps = entry.payload.get("plan", {}).get("steps", [])
            if steps:
                _icon = {"pending": "⬜", "running": "🔄", "done": "✅", "skipped": "⏭️"}
                step_lines = ["  \n  **步骤进度**："]
                for i, s in enumerate(steps, 1):
                    icon = _icon.get(s.get("status", "pending"), "⬜")
                    step_lines.append(f"  {i}. {icon} {s.get('title', '')}")
                lines.append("\n".join(step_lines))

        lines.append("")  # 空行分隔

    return "\n".join(lines)


def render_turns_index(turns: list[Turn]) -> str:
    """渲染所有 Turn 的概览索引（用于历史面板）。"""
    if not turns:
        return "*（本 session 暂无历史记录）*"

    lines = ["### 📚 历史轮次概览", ""]
    for turn in reversed(turns):  # 最新的在前
        dur = f"{turn.duration_s:.1f}s" if turn.duration_s else "进行中"
        entry_count = len(turn.entries)
        tool_calls = sum(1 for e in turn.entries if e.event_type == "tool_call")
        lines.append(
            f"- **Turn {turn.turn_id}** `{turn.started_at.strftime('%H:%M:%S')}`"
            f" · {dur} · {entry_count} 个事件 · {tool_calls} 次工具调用"
        )
        lines.append(f"  > {turn.user_message[:80]}")
        lines.append("")

    return "\n".join(lines)
