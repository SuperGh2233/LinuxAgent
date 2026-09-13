"""Gradio Web 入口。

启动：
    python app_web.py [--host 0.0.0.0] [--port 7860] [--no-safety]

特性：
- Gradio Blocks + ChatInterface 流式对话
- 侧边栏实时显示会话状态、工具调用历史
- 风险确认卡片弹出区（HIGH 级别时激活，用户点击同意/拒绝）
- Plan-Act 可视化看板（对话区下方全宽）
- 思考过程面板：实时滚动 + 历史轮次查阅
- SessionPool 支持多浏览器标签隔离（LRU 上限 20）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Any

import gradio as gr
from loguru import logger

from linuxagent.config import get_settings
from linuxagent.core.agent import Agent, build_agent
from linuxagent.core.trace_store import get_trace_store
from linuxagent.ui.session_pool import SessionPool
from linuxagent.ui.trace_view import render_turn_timeline, render_turns_index
from linuxagent.ui.web_components import (
    format_risk_card,
    run_agent_streaming,
)
from linuxagent.utils.logger import setup_logging

_pool: SessionPool | None = None
_enable_safety: bool = True


def _get_pool() -> SessionPool:
    global _pool
    if _pool is None:
        settings = get_settings()

        def _factory(sid: str) -> Agent:
            return build_agent(session_id=sid, enable_safety=_enable_safety)

        _pool = SessionPool(max_size=20, ttl_seconds=1800, agent_factory=_factory)
    return _pool


def _get_agent(session_state: dict[str, Any]) -> tuple[str, Agent]:
    sid = session_state.get("session_id")
    pool = _get_pool()
    sid, agent = pool.get_or_create(sid)
    session_state["session_id"] = sid
    return sid, agent


def _read_syslog(lines: int = 200) -> str:
    """读取 loguru 系统日志末尾 N 行（自动匹配 logs/ 下最新的 .log 文件）。"""
    from pathlib import Path
    log_dir = Path("logs")
    if not log_dir.exists():
        return "（日志目录尚不存在，运行一次对话后自动生成）"

    # 优先读 app.log（当前活跃日志），其次找最新的 .log 文件
    log_path = log_dir / "app.log"
    if not log_path.exists():
        candidates = sorted(
            [p for p in log_dir.glob("*.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        log_path = candidates[0] if candidates else None

    if not log_path or not log_path.exists():
        return "（日志文件尚不存在，运行一次对话后自动生成）"
    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return "\n".join(tail)
    except Exception as e:
        return f"（读取失败：{e}）"


def _read_tracelog(session_id: str = "", lines: int = 100) -> str:
    """读取最新一个 JSONL trace 文件末尾 N 行，或指定 session 的 JSONL。"""
    import json
    from pathlib import Path
    trace_dir = Path("logs/traces")
    if not trace_dir.exists():
        return "（trace 目录尚不存在，运行一次对话后自动生成）"

    # 找到最新的 JSONL 文件（或指定 session）
    if session_id:
        target = trace_dir / f"{session_id}.jsonl"
    else:
        files = sorted(trace_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        target = files[0] if files else None

    if not target or not target.exists():
        return "（暂无操作记录，发送一条指令后自动生成）"

    try:
        all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        # 美化 JSON 输出
        pretty_lines = []
        for raw in tail:
            try:
                obj = json.loads(raw)
                ts = obj.get("ts", "")[:19].replace("T", " ")
                ev = obj.get("event_type", "?")
                summary = obj.get("summary", "")
                turn = obj.get("turn_id", "?")
                seq = obj.get("seq", "?")
                pretty_lines.append(f"[{ts}] T{turn}#{seq:02d} {ev:22s} {summary}")
            except Exception:
                pretty_lines.append(raw)
        return "\n".join(pretty_lines)
    except Exception as e:
        return f"（读取失败：{e}）"


def _render_trace(session_id: str, turn_id: int | None = None) -> str:
    """从 TraceStore 拉取最新思考链，渲染为 Markdown。"""
    try:
        store = get_trace_store()
        if turn_id is not None:
            turn = store.get_turn(session_id, turn_id)
        else:
            turn = store.get_latest_turn(session_id)
        if turn is None:
            return "*（暂无记录）*"
        return render_turn_timeline(turn, show_payload=False, show_plan_steps=True)
    except Exception as e:
        return f"*（渲染失败：{e}）*"


def _render_history_index(session_id: str) -> str:
    """渲染历史轮次索引。"""
    try:
        store = get_trace_store()
        turns = store.get_turns(session_id)
        return render_turns_index(turns)
    except Exception:
        return "*（暂无历史）*"


def _get_turn_choices(session_id: str) -> list[str]:
    """返回历史轮次下拉选项列表。"""
    try:
        store = get_trace_store()
        turns = store.get_turns(session_id)
        return [t.label for t in reversed(turns)]
    except Exception:
        return []


def _is_approve_text(text: str) -> bool:
    t = text.strip().lower()
    return any(k in t for k in ("确认", "同意", "继续", "yes", "y", "ok", "approve"))


def _is_reject_text(text: str) -> bool:
    t = text.strip().lower()
    return any(k in t for k in ("拒绝", "取消", "停止", "no", "n", "reject", "cancel"))


# ─────────────────────────────────────────────────────────────
# 输出元组顺序（共 9 个）：
#   0  chatbot         ← 对话历史
#   1  status_md       ← 右侧状态栏
#   2  confirm_md      ← 风险确认 Markdown
#   3  confirm_group   ← 风险确认面板（visible）
#   4  session_state
#   5  confirm_state
#   6  plan_board      ← 右侧执行计划看板
#   7  trace_md        ← 底部实时思考链（当前 turn）
#   8  history_md      ← 历史轮次索引（每轮结束后刷新）
# ─────────────────────────────────────────────────────────────

async def _chat_fn(
    message: str,
    history: list[dict[str, str]],
    session_state: dict[str, Any],
    confirm_state: dict[str, Any],
) -> Any:
    """主聊天回调（流式 generator）。"""
    if not message.strip():
        yield (history, gr.update(), gr.update(), gr.update(visible=False),
               session_state, confirm_state, gr.update(), gr.update(), gr.update())
        return

    # 若当前处于等待确认状态，则把“确认/拒绝”文本视作按钮操作，避免误开新轮次
    if confirm_state.get("pending", False):
        if _is_approve_text(message):
            async for out in _confirm_fn(True, history, session_state, confirm_state):
                yield out
            return
        if _is_reject_text(message):
            async for out in _confirm_fn(False, history, session_state, confirm_state):
                yield out
            return
        # 非确认文本：提示用户先完成确认，不启动新任务
        sid, _ = _get_agent(session_state)
        hinted_history = list(history)
        hinted_history.append({"role": "user", "content": message})
        hinted_history.append(
            {
                "role": "assistant",
                "content": "⏸️ 当前有高风险操作等待确认，请先点击“同意执行/拒绝”，或输入“确认/拒绝”。",
            }
        )
        yield (
            hinted_history,
            gr.update(value="🟠 **等待确认** · ⏸️ 高风险操作挂起"),
            gr.update(),              # confirm_md：内容不变
            gr.update(visible=True),  # confirm_group：保持可见
            session_state,
            confirm_state,
            gr.update(),
            gr.update(),
            gr.update(),
        )
        return

    sid, agent = _get_agent(session_state)

    # 新轮次开始：隐藏旧确认卡片，重置思考链占位
    yield (
        history,
        gr.update(value="🟢 **已连接** · 🤔 规划中..."),
        gr.update(),              # confirm_md
        gr.update(visible=False), # confirm_group：隐藏
        session_state,
        confirm_state,
        gr.update(value="*⏳ 正在生成执行计划...*"),
        gr.update(value="*⏳ 正在思考...*"),
        gr.update(),
    )

    last_hist = history  # 追踪最后一次更新的 chatbot 历史
    async for hist, status, need_confirm, confirm_md, plan_md in run_agent_streaming(
        agent, message, history
    ):
        last_hist = hist
        trace_md = _render_trace(sid)

        if need_confirm:
            confirm_state["pending"] = True
            yield (
                hist,
                gr.update(value=f"🟠 **等待确认** · {status}"),
                gr.update(value=confirm_md),  # confirm_md：更新内容
                gr.update(visible=True),      # confirm_group：显示含按钮的整个卡片
                session_state,
                confirm_state,
                gr.update(value=plan_md),
                gr.update(value=trace_md),
                gr.update(),
            )
            return
        else:
            yield (
                hist,
                gr.update(value=f"🟢 **已连接** · {status}"),
                gr.update(),              # confirm_md
                gr.update(visible=False), # confirm_group：隐藏
                session_state,
                confirm_state,
                gr.update(value=plan_md),
                gr.update(value=trace_md),
                gr.update(),
            )

    # 轮次结束：刷新历史索引
    history_md = _render_history_index(sid)
    yield (
        last_hist,
        gr.update(),
        gr.update(),              # confirm_md
        gr.update(visible=False), # confirm_group：隐藏
        session_state,
        confirm_state,
        gr.update(),
        gr.update(value=_render_trace(sid)),
        gr.update(value=history_md),
    )


def _content_to_text(content: Any) -> str:
    """把 Chatbot content 归一化为字符串，兼容 str/list/None。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


async def _confirm_fn(
    approved: bool,
    history: list[dict[str, str]],
    session_state: dict[str, Any],
    confirm_state: dict[str, Any],
) -> Any:
    """用户点击同意/拒绝按钮后的处理（实时流式 yield，含 plan + trace 刷新）。

    处理链路：
    1. 调用 agent.resume(approved) 继续主循环
    2. 实时渲染 tool_call / tool_result / risk_warning / final / error 事件
    3. 若 resume 期间又触发新的 confirmation_required，
       立即重新弹出确认卡片并挂起（与第一次相同的逻辑）
    """
    confirm_state["pending"] = False
    sid, agent = _get_agent(session_state)
    status = "✅ 同意执行中..." if approved else "❌ 已拒绝"

    final_hist = list(history)
    plan_md_value = gr.update()
    current_response_ref: list[str] = [""]  # 用列表包装，供内层函数修改

    def _append_to_last_assistant(text: str) -> None:
        current_response_ref[0] += text
        for i in range(len(final_hist) - 1, -1, -1):
            if final_hist[i].get("role") == "assistant":
                base = _content_to_text(final_hist[i].get("content"))
                final_hist[i] = {"role": "assistant", "content": base + text}
                return
        final_hist.append({"role": "assistant", "content": text})

    async for ev in agent.resume(approved=approved):
        from linuxagent.ui.web_components import format_tool_result, format_tool_call_badge

        if ev.type == "plan_updated":
            from linuxagent.core.planner import Plan
            plan = Plan(**ev.payload["plan"])
            plan_md_value = gr.update(value=plan.render_markdown())

        elif ev.type == "thinking":
            step = ev.payload.get("step", 0)
            status = f"🔄 ReAct 步骤 {step + 1}..."
            # thinking 只更新状态栏，不写入对话

        elif ev.type == "tool_call":
            tc_name = ev.payload.get("name", "?")
            status = f"🔧 调用工具：{tc_name}"
            # tool_call 只更新状态栏，tool_result 会用 details 块展示完整输出

        elif ev.type == "tool_result":
            formatted = format_tool_result(ev.payload)
            _append_to_last_assistant(f"\n\n{formatted}")
            status = "✅ 工具执行完成"

        elif ev.type == "risk_warning":
            card = format_risk_card(ev.payload)
            _append_to_last_assistant(f"\n\n{card}")

        elif ev.type == "confirmation_required":
            # resume 过程中又遇到新的高风险操作：重新弹出确认卡片并挂起
            from linuxagent.safety.confirm import ConfirmationPrompt
            from linuxagent.safety.risk_engine import RiskAssessment
            from linuxagent.utils.helpers import render_command_preview

            assessment = ev.payload.get("assessment", {})
            tc_payload = ev.payload.get("tool_call", {})
            rendered = render_command_preview(
                tc_payload.get("name", "?"), tc_payload.get("arguments", {})
            )
            cp = ConfirmationPrompt(
                tool_call_id=tc_payload.get("id", ""),
                tool_name=tc_payload.get("name", "?"),
                arguments=tc_payload.get("arguments", {}),
                rendered_cmd=rendered,
                assessment=RiskAssessment(**assessment),
            )
            new_confirm_md = cp.to_markdown()
            _append_to_last_assistant("\n\n> ⏸️ **新的高风险操作，等待您确认**")
            confirm_state["pending"] = True
            status = "⏸️ 等待用户确认..."
            trace_md = _render_trace(sid)
            yield (
                final_hist,
                gr.update(value=f"🟠 **等待确认** · {status}"),
                gr.update(value=new_confirm_md),  # confirm_md：更新内容
                gr.update(visible=True),           # confirm_group：显示卡片
                session_state,
                confirm_state,
                plan_md_value,
                gr.update(value=trace_md),
                gr.update(),
            )
            return  # 挂起，等待用户再次确认

        elif ev.type == "final":
            content = ev.payload.get("content", "")
            _append_to_last_assistant(f"\n\n---\n\n{content}")
            status = "✅ 完成"

        elif ev.type == "error":
            reason = ev.payload.get("reason", "?")
            _append_to_last_assistant(f"\n\n---\n\n❌ **操作失败**：{reason}")
            status = f"❌ {reason}"

        trace_md = _render_trace(sid)
        yield (
            final_hist,
            gr.update(value=f"🟢 **已连接** · {status}"),
            gr.update(visible=False),
            gr.update(visible=False),
            session_state,
            confirm_state,
            plan_md_value,
            gr.update(value=trace_md),
            gr.update(),
        )

    # 结束后刷新历史索引
    yield (
        final_hist,
        gr.update(value=f"🟢 **已连接** · {status}"),
        gr.update(visible=False),
        gr.update(visible=False),
        session_state,
        confirm_state,
        plan_md_value,
        gr.update(value=_render_trace(sid)),
        gr.update(value=_render_history_index(sid)),
    )


def _load_turn_trace(turn_label: str, session_state: dict[str, Any]) -> str:
    """根据下拉选择器的标签，加载指定 Turn 的思考链。"""
    if not turn_label:
        return "*（请选择一个轮次）*"
    sid = session_state.get("session_id", "")
    if not sid:
        return "*（session 未初始化）*"
    try:
        store = get_trace_store()
        turns = store.get_turns(sid)
        for turn in turns:
            if turn.label == turn_label:
                return render_turn_timeline(turn, show_payload=True, show_plan_steps=True)
        return "*（未找到该轮次）*"
    except Exception as e:
        return f"*（加载失败：{e}）*"


_UI_CSS = """
/* ══════════════════════════════════════
   全局 & 容器
══════════════════════════════════════ */
.gradio-container {
    max-width: 1440px !important;
    font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
}

/* ── 顶部品牌 Banner ── */
#app_header {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 60%, #0c4a6e 100%);
    border-radius: 14px;
    padding: 20px 28px 16px;
    margin-bottom: 16px;
    box-shadow: 0 4px 20px rgba(15,23,42,0.3);
}
#app_header h1 {
    color: #f0f9ff !important;
    font-size: 1.6em !important;
    font-weight: 700 !important;
    margin: 0 0 4px !important;
    letter-spacing: -0.3px;
}
#app_header p {
    color: #94a3b8 !important;
    font-size: 0.88em !important;
    margin: 0 !important;
}

/* ══════════════════════════════════════
   对话框
══════════════════════════════════════ */
#chatbot {
    border-radius: 14px !important;
    border: 1px solid #e2e8f0 !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06) !important;
}
/* 用户消息气泡 */
#chatbot .message.user {
    background: linear-gradient(135deg, #dbeafe, #eff6ff) !important;
    color: #1e3a5f !important;
    border-radius: 14px 14px 4px 14px !important;
    border: 1px solid #bfdbfe !important;
}
/* 助手消息气泡 */
#chatbot .message.bot {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 14px 14px 14px 4px !important;
}
/* details 折叠块 */
#chatbot details {
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 10px;
    margin: 6px 0;
    font-size: 0.85em;
}
#chatbot details summary {
    cursor: pointer;
    color: #475569;
    font-weight: 500;
    padding: 2px 0;
}
#chatbot details summary:hover { color: #1e40af; }
#chatbot details[open] { background: #fff; }

/* ── 输入框 ── */
#user_input textarea {
    border-radius: 10px !important;
    border: 1.5px solid #cbd5e1 !important;
    font-size: 0.95em !important;
    transition: border-color 0.2s;
}
#user_input textarea:focus {
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.12) !important;
}
#send_btn {
    border-radius: 10px !important;
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px !important;
    box-shadow: 0 2px 8px rgba(37,99,235,0.3) !important;
    transition: transform 0.1s, box-shadow 0.1s !important;
}
#send_btn:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(37,99,235,0.4) !important;
}
#send_btn:active { transform: translateY(0) !important; }

/* ══════════════════════════════════════
   风险确认卡片
══════════════════════════════════════ */
#confirm_group {
    border: 2px solid #f59e0b !important;
    border-radius: 14px !important;
    padding: 14px 16px !important;
    background: linear-gradient(135deg, #fffbeb, #fff7ed) !important;
    box-shadow: 0 4px 16px rgba(245,158,11,0.2) !important;
    animation: pulse-border 2s ease-in-out infinite;
}
@keyframes pulse-border {
    0%, 100% { box-shadow: 0 4px 16px rgba(245,158,11,0.2); }
    50%       { box-shadow: 0 4px 24px rgba(245,158,11,0.4); }
}
#approve_btn {
    border-radius: 10px !important;
    background: linear-gradient(135deg, #059669, #047857) !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 8px rgba(5,150,105,0.3) !important;
}
#reject_btn {
    border-radius: 10px !important;
    font-weight: 600 !important;
}

/* ══════════════════════════════════════
   右侧边栏
══════════════════════════════════════ */
#status_col {
    border-left: 2px solid #e2e8f0;
    padding-left: 14px;
}

/* 状态指示器 */
#status_bar {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 0.85em;
    line-height: 1.6;
}

/* Plan 看板 */
#plan_board {
    background: linear-gradient(135deg, #eff6ff 0%, #f0f9ff 100%);
    border: 1.5px solid #bfdbfe;
    border-radius: 12px;
    padding: 12px 14px;
    min-height: 72px;
    font-size: 0.88em;
    line-height: 1.7;
    box-shadow: 0 2px 8px rgba(59,130,246,0.08);
}
#plan_board p { margin: 2px 0 !important; }

/* 风险等级徽章区 */
#risk_legend {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 0.82em;
    line-height: 2;
}

/* 快捷指令 Examples */
.gr-examples { border-radius: 10px !important; }
.gr-examples .label { display: none !important; }
.gr-examples button {
    font-size: 0.82em !important;
    padding: 4px 10px !important;
    border-radius: 6px !important;
    background: #f1f5f9 !important;
    border: 1px solid #e2e8f0 !important;
    color: #334155 !important;
    transition: background 0.15s !important;
}
.gr-examples button:hover {
    background: #dbeafe !important;
    border-color: #93c5fd !important;
    color: #1e40af !important;
}

/* ══════════════════════════════════════
   底部 Tabs
══════════════════════════════════════ */
.tabs > .tab-nav { border-bottom: 2px solid #e2e8f0 !important; }
.tabs > .tab-nav button {
    font-size: 0.88em !important;
    font-weight: 500 !important;
    color: #64748b !important;
    border-radius: 6px 6px 0 0 !important;
    padding: 8px 16px !important;
}
.tabs > .tab-nav button.selected {
    color: #1e40af !important;
    border-bottom: 2px solid #2563eb !important;
    font-weight: 600 !important;
}

/* 思考链面板 */
#trace_panel {
    background: #0f172a;
    color: #94a3b8;
    border-radius: 10px;
    padding: 14px 16px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.82em;
    line-height: 1.7;
    max-height: 480px;
    overflow-y: auto;
}
#trace_panel p  { color: #cbd5e1; margin: 3px 0; }
#trace_panel strong { color: #38bdf8; }
#trace_panel code { color: #a78bfa; background: transparent; }

/* 历史详情 */
#history_detail {
    font-size: 0.84em;
    max-height: 500px;
    overflow-y: auto;
    background: #fafafa;
    border-radius: 8px;
    padding: 10px;
}

/* ── 系统日志面板 ── */
#syslog_panel {
    background: #0a0f1a;
    color: #64ff8c;
    border-radius: 10px;
    padding: 14px 16px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.80em;
    line-height: 1.65;
    max-height: 420px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    border: 1px solid #1e3a2e;
}
#syslog_panel p { color: #64ff8c; margin: 0; }
#tracelog_panel {
    background: #0f0a1a;
    color: #c084fc;
    border-radius: 10px;
    padding: 14px 16px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78em;
    line-height: 1.65;
    max-height: 420px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    border: 1px solid #2d1a4a;
}
#tracelog_panel p { color: #c084fc; margin: 0; }

/* ── 分隔线美化 ── */
hr { border: none; border-top: 1px solid #e2e8f0; margin: 12px 0; }

/* ── 右侧小节标题 ── */
.sidebar-section-title {
    font-size: 0.78em !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.8px !important;
    color: #94a3b8 !important;
    margin: 12px 0 6px !important;
}
"""


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="LinuxAgent · 操作系统智能代理") as demo:

        # ══════════════════════════════════════════
        # 顶部品牌 Banner
        # ══════════════════════════════════════════
        gr.HTML("""
        <div id="app_header">
            <h1>🤖 LinuxAgent &nbsp;·&nbsp; 操作系统智能代理</h1>
            <p>用自然语言操控远端 Linux 服务器 &nbsp;·&nbsp; 三闸安全防护 &nbsp;·&nbsp; Plan-Act 可视化规划 &nbsp;·&nbsp; 全程推理追踪</p>
        </div>
        """)

        session_state = gr.State(value={})
        confirm_state = gr.State(value={"pending": False})

        # ══════════════════════════════════════════
        # 主区域：左（对话）+ 右（状态/计划/快捷）
        # ══════════════════════════════════════════
        with gr.Row(equal_height=False):
            # ── 左侧：主对话区（占 5/7）──
            with gr.Column(scale=5):
                chatbot = gr.Chatbot(
                    height=580,
                    label="💬 对话",
                    elem_id="chatbot",
                    avatar_images=(None, "https://api.dicebear.com/7.x/bottts/svg?seed=linux"),
                    latex_delimiters=[],
                )
                with gr.Row():
                    user_input = gr.Textbox(
                        placeholder="✏️  输入运维指令，例如：查看根目录磁盘使用情况",
                        label="",
                        scale=6,
                        show_label=False,
                        lines=1,
                        elem_id="user_input",
                    )
                    send_btn = gr.Button(
                        "发送 ▶", variant="primary", scale=1,
                        min_width=80, elem_id="send_btn",
                    )

                # ── 风险确认卡片（紧贴输入框下方）──
                with gr.Group(visible=False, elem_id="confirm_group") as confirm_group:
                    confirm_md = gr.Markdown("", label="⚠️ 风险确认")
                    with gr.Row():
                        approve_btn = gr.Button(
                            "✅ 同意执行", variant="primary", scale=1, elem_id="approve_btn"
                        )
                        reject_btn = gr.Button(
                            "❌ 拒绝操作", variant="stop", scale=1, elem_id="reject_btn"
                        )

            # ── 右侧：状态 + Plan 看板 + 快捷指令（占 2/7）──
            with gr.Column(scale=2, min_width=290, elem_id="status_col"):

                # 状态指示器
                gr.HTML('<p class="sidebar-section-title">▸ 运行状态</p>')
                status_md = gr.Markdown("⬜ 等待连接...", elem_id="status_bar")

                # Plan 看板
                gr.HTML('<p class="sidebar-section-title">▸ 实时执行计划</p>')
                plan_board = gr.Markdown(
                    "*发送指令后，执行计划将实时更新...*",
                    elem_id="plan_board",
                )

                # 风险等级说明
                gr.HTML('<p class="sidebar-section-title">▸ 安全等级</p>')
                gr.HTML("""
                <div id="risk_legend">
                    🟥 <b>BLOCKED</b> 直接拦截，不可绕过<br>
                    🟧 <b>HIGH</b> &nbsp;&nbsp; 需二次确认方可执行<br>
                    🟨 <b>MEDIUM</b> 自动执行 + 风险提示<br>
                    🟩 <b>LOW</b> &nbsp;&nbsp;&nbsp; 自动放行
                </div>
                """)

                # 快捷指令
                gr.HTML('<p class="sidebar-section-title">▸ 快捷指令</p>')
                gr.Examples(
                    examples=[
                        ["查看根目录磁盘使用情况"],
                        ["列出 CPU 占用最高的 10 个进程"],
                        ["80 端口是谁在监听？"],
                        ["查看系统负载和运行时长"],
                        ["列出所有普通用户"],
                        ["查看 /var/log/messages 最后 30 行"],
                        ["找出 /home 目录下最大的 5 个文件"],
                        ["检查 sshd 服务状态"],
                    ],
                    inputs=user_input,
                    label="",
                )

        # ══════════════════════════════════════════
        # 底部辅助面板：思考链 + 历史查阅（Tab 布局）
        # ══════════════════════════════════════════
        with gr.Tabs():
            with gr.Tab("🧠 实时推理链"):
                gr.Markdown(
                    "_每一步推理的完整时间线：规划生成 → 工具调用 → 风险评估 → 结果处理 → 最终回复_",
                )
                trace_md = gr.Markdown(
                    "*发送指令后，推理过程将实时显示在这里...*",
                    elem_id="trace_panel",
                )

            with gr.Tab("📚 历史轮次回溯"):
                with gr.Row():
                    with gr.Column(scale=1):
                        history_md = gr.Markdown(
                            "*完成对话后，历史轮次将显示在这里...*",
                            label="轮次总览",
                        )
                    with gr.Column(scale=2):
                        turn_selector = gr.Dropdown(
                            choices=[],
                            label="🔍 选择轮次查看完整推理链",
                            interactive=True,
                        )
                        history_detail = gr.Markdown(
                            "*选择上方轮次后，完整推理链（含工具调用 payload）将显示在这里*",
                            elem_id="history_detail",
                        )

            with gr.Tab("📄 系统日志"):
                gr.Markdown("_loguru 系统日志（`logs/linuxagent.log`）· 包含每次 LLM 调用、SSH 执行、风险评估的结构化记录_")
                with gr.Row():
                    syslog_lines = gr.Slider(
                        minimum=50, maximum=500, value=150, step=50,
                        label="显示行数", scale=3,
                    )
                    syslog_refresh_btn = gr.Button("🔄 刷新", scale=1, variant="secondary")
                syslog_panel = gr.Textbox(
                    value=_read_syslog(150),
                    lines=20,
                    max_lines=24,
                    label="",
                    show_label=False,
                    interactive=False,
                    elem_id="syslog_panel",
                )
                syslog_refresh_btn.click(
                    fn=_read_syslog,
                    inputs=[syslog_lines],
                    outputs=[syslog_panel],
                )
                syslog_lines.change(
                    fn=_read_syslog,
                    inputs=[syslog_lines],
                    outputs=[syslog_panel],
                )

            with gr.Tab("🗂️ 操作记录 (TraceStore)"):
                gr.Markdown("_TraceStore JSONL（`logs/traces/<session_id>.jsonl`）· 每行一条 AgentEvent，包含 LLM 内部决策过程的完整审计链_")
                with gr.Row():
                    trace_lines = gr.Slider(
                        minimum=20, maximum=300, value=80, step=20,
                        label="显示条数", scale=3,
                    )
                    trace_refresh_btn = gr.Button("🔄 刷新", scale=1, variant="secondary")
                tracelog_panel = gr.Textbox(
                    value=_read_tracelog(lines=80),
                    lines=20,
                    max_lines=24,
                    label="",
                    show_label=False,
                    interactive=False,
                    elem_id="tracelog_panel",
                )
                trace_refresh_btn.click(
                    fn=lambda n, ss: _read_tracelog(
                        session_id=ss.get("session_id", ""), lines=int(n)
                    ),
                    inputs=[trace_lines, session_state],
                    outputs=[tracelog_panel],
                )
                trace_lines.change(
                    fn=lambda n, ss: _read_tracelog(
                        session_id=ss.get("session_id", ""), lines=int(n)
                    ),
                    inputs=[trace_lines, session_state],
                    outputs=[tracelog_panel],
                )

        # 选择轮次后加载详情
        turn_selector.change(
            fn=_load_turn_trace,
            inputs=[turn_selector, session_state],
            outputs=[history_detail],
        )

        # ── 刷新历史下拉 ──
        def _refresh_turn_choices(ss: dict[str, Any]) -> Any:
            sid = ss.get("session_id", "")
            choices = _get_turn_choices(sid) if sid else []
            return gr.update(choices=choices, value=choices[0] if choices else None)

        # ── 同步输入框提示词：待确认时提示“确认/拒绝” ──
        def _sync_input_placeholder(cs: dict[str, Any]) -> Any:
            if cs.get("pending", False):
                return gr.update(
                    placeholder="当前有高风险操作待确认：请输入 确认 / 拒绝，或点击下方按钮"
                )
            return gr.update(placeholder="输入运维指令，例如：查看根目录磁盘使用情况")

        # 9 个输出
        chat_outputs = [
            chatbot, status_md, confirm_md, confirm_group,
            session_state, confirm_state,
            plan_board, trace_md, history_md,
        ]

        async def _wrapped_chat(msg, hist, ss, cs):
            async for out in _chat_fn(msg, hist, ss, cs):
                yield out[0], out[1], out[2], out[3], out[4], out[5], out[6], out[7], out[8]

        def _refresh_syslog(_n=150):
            return gr.update(value=_read_syslog(150))

        def _refresh_tracelog(ss: dict[str, Any]):
            return gr.update(value=_read_tracelog(
                session_id=ss.get("session_id", ""), lines=80
            ))

        send_btn.click(
            fn=_wrapped_chat,
            inputs=[user_input, chatbot, session_state, confirm_state],
            outputs=chat_outputs,
        ).then(
            fn=lambda: gr.update(value=""), outputs=[user_input]
        ).then(
            fn=_refresh_turn_choices, inputs=[session_state], outputs=[turn_selector]
        ).then(
            fn=_sync_input_placeholder, inputs=[confirm_state], outputs=[user_input]
        ).then(
            fn=_refresh_syslog, outputs=[syslog_panel]
        ).then(
            fn=_refresh_tracelog, inputs=[session_state], outputs=[tracelog_panel]
        )

        user_input.submit(
            fn=_wrapped_chat,
            inputs=[user_input, chatbot, session_state, confirm_state],
            outputs=chat_outputs,
        ).then(
            fn=lambda: gr.update(value=""), outputs=[user_input]
        ).then(
            fn=_refresh_turn_choices, inputs=[session_state], outputs=[turn_selector]
        ).then(
            fn=_sync_input_placeholder, inputs=[confirm_state], outputs=[user_input]
        ).then(
            fn=_refresh_syslog, outputs=[syslog_panel]
        ).then(
            fn=_refresh_tracelog, inputs=[session_state], outputs=[tracelog_panel]
        )

        async def _approve(hist, ss, cs):
            async for out in _confirm_fn(True, hist, ss, cs):
                yield out[0], out[1], out[2], out[3], out[4], out[5], out[6], out[7], out[8]

        async def _reject(hist, ss, cs):
            async for out in _confirm_fn(False, hist, ss, cs):
                yield out[0], out[1], out[2], out[3], out[4], out[5], out[6], out[7], out[8]

        approve_btn.click(
            fn=_approve,
            inputs=[chatbot, session_state, confirm_state],
            outputs=chat_outputs,
        ).then(fn=_refresh_turn_choices, inputs=[session_state], outputs=[turn_selector]).then(
            fn=_sync_input_placeholder, inputs=[confirm_state], outputs=[user_input]
        ).then(fn=_refresh_syslog, outputs=[syslog_panel]).then(
            fn=_refresh_tracelog, inputs=[session_state], outputs=[tracelog_panel]
        )

        reject_btn.click(
            fn=_reject,
            inputs=[chatbot, session_state, confirm_state],
            outputs=chat_outputs,
        ).then(fn=_refresh_turn_choices, inputs=[session_state], outputs=[turn_selector]).then(
            fn=_sync_input_placeholder, inputs=[confirm_state], outputs=[user_input]
        ).then(fn=_refresh_syslog, outputs=[syslog_panel]).then(
            fn=_refresh_tracelog, inputs=[session_state], outputs=[tracelog_panel]
        )

    return demo


def main() -> None:
    parser = argparse.ArgumentParser(description="LinuxAgent Web Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--no-safety", action="store_true", help="禁用风险引擎（调试用）")
    args = parser.parse_args()

    global _enable_safety
    _enable_safety = not args.no_safety

    settings = get_settings()
    setup_logging(
        level=settings.logging.level,
        log_file=settings.logging.file,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
    )

    logger.info("Starting LinuxAgent Web: {}:{} safety={}", args.host, args.port, _enable_safety)
    demo = build_ui()
    demo.queue(max_size=20)
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        show_error=True,
        theme=gr.themes.Soft(),
        css=_UI_CSS,
    )


if __name__ == "__main__":
    main()
