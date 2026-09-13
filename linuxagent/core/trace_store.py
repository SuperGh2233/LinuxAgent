"""思考过程存储：把每次对话的完整 AgentEvent 时间线持久化。

设计原则
--------
- **内存优先**：``TraceStore`` 将所有条目保存在内存字典中，适合演示场景。
- **可选落盘**：若构造时传入 ``log_dir``，每条 ``TraceEntry`` 会追加写入
  ``<log_dir>/<session_id>.jsonl``，方便赛后复盘。
- **零侵入**：Agent 只需在事件产生时调用 ``store.append(session_id, turn_id, ev)``，
  不改变任何核心逻辑。

数据结构
--------
``Turn``：一次 ``agent.chat()`` 调用对应一个 Turn，包含：
  - ``turn_id``：单调递增整数
  - ``user_message``：用户输入
  - ``entries``：按时序排列的 ``TraceEntry`` 列表

``TraceEntry``：对 ``AgentEvent`` 的薄封装，额外记录人类可读摘要 ``summary``。
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────

class TraceEntry(BaseModel):
    """单个 AgentEvent 的存储表示。"""

    turn_id: int
    seq: int                          # 在当前 turn 内的序号，从 0 开始
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""                 # 人类可读的一行摘要
    ts: datetime = Field(default_factory=datetime.utcnow)


class Turn(BaseModel):
    """一轮对话（一次 chat() 调用）的完整记录。"""

    turn_id: int
    user_message: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    entries: list[TraceEntry] = Field(default_factory=list)

    @property
    def label(self) -> str:
        """下拉选择器用的标签：时间 + 用户消息摘要。"""
        ts_str = self.started_at.strftime("%H:%M:%S")
        msg = self.user_message[:40] + ("…" if len(self.user_message) > 40 else "")
        return f"[{ts_str}] Turn {self.turn_id} · {msg}"

    @property
    def duration_s(self) -> float | None:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


# ─────────────────────────────────────────────────────────────
# 摘要生成器
# ─────────────────────────────────────────────────────────────

def _make_summary(event_type: str, payload: dict[str, Any]) -> str:
    """从事件类型和 payload 生成一行人类可读摘要。"""
    if event_type == "thinking":
        return f"🤔 思考中（Step {payload.get('step', 0) + 1}）"

    if event_type == "plan_created":
        plan = payload.get("plan", {})
        goal = plan.get("goal", "")
        n = len(plan.get("steps", []))
        return f"📋 生成计划：{goal}（{n} 步）"

    if event_type == "plan_updated":
        steps = payload.get("plan", {}).get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        running = [s.get("title", "") for s in steps if s.get("status") == "running"]
        running_str = f" — 执行中：{running[0]}" if running else ""
        return f"🔄 计划更新（已完成 {done}/{len(steps)} 步{running_str}）"

    if event_type == "tool_call":
        name = payload.get("name", "?")
        args = payload.get("arguments", {})
        brief = ", ".join(f"{k}={str(v)[:30]!r}" for k, v in list(args.items())[:2])
        return f"🔧 调用工具 `{name}`({brief})"

    if event_type == "tool_result":
        name = payload.get("tool_name", "?")
        ok = payload.get("success", False)
        icon = "✅" if ok else "❌"
        content = (payload.get("content") or "")[:80].replace("\n", " ")
        return f"{icon} `{name}` 返回：{content}"

    if event_type == "risk_warning":
        level = payload.get("level", "?")
        reason = payload.get("reason", "")[:60]
        return f"⚠️ 风险：{level} — {reason}"

    if event_type == "confirmation_required":
        tc = payload.get("tool_call", {})
        return f"⏸️ 等待确认：`{tc.get('name', '?')}`"

    if event_type == "final":
        content = (payload.get("content") or "")[:100].replace("\n", " ")
        return f"🏁 最终回复：{content}"

    if event_type == "error":
        return f"💥 错误：{payload.get('reason', '?')}"

    return f"· {event_type}"


# ─────────────────────────────────────────────────────────────
# TraceStore
# ─────────────────────────────────────────────────────────────

class TraceStore:
    """线程不安全的内存 TraceStore（单进程 Gradio 足够用）。

    Args:
        log_dir: 若不为 None，则同时将每条 entry 以 JSONL 格式追加写入
                 ``<log_dir>/<session_id>.jsonl``。
        max_turns_per_session: 每个 session 最多保留最近 N 轮，防止内存无限增长。
    """

    def __init__(
        self,
        log_dir: str | Path | None = None,
        max_turns_per_session: int = 50,
    ) -> None:
        # session_id → list[Turn]（按 turn_id 升序）
        self._turns: dict[str, list[Turn]] = defaultdict(list)
        self._max_turns = max_turns_per_session
        self._log_dir: Path | None = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── 写入接口 ──

    def new_turn(self, session_id: str, user_message: str) -> int:
        """创建新 Turn，返回 turn_id（从 1 开始）。"""
        turns = self._turns[session_id]
        turn_id = len(turns) + 1
        turns.append(Turn(turn_id=turn_id, user_message=user_message))
        # LRU 裁剪
        if len(turns) > self._max_turns:
            self._turns[session_id] = turns[-self._max_turns:]
        return turn_id

    def append(
        self,
        session_id: str,
        turn_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEntry:
        """追加一条 TraceEntry，返回写入的条目。"""
        turn = self._get_turn(session_id, turn_id)
        if turn is None:
            return TraceEntry(turn_id=turn_id, seq=0, event_type=event_type, payload=payload)

        seq = len(turn.entries)
        summary = _make_summary(event_type, payload)
        entry = TraceEntry(
            turn_id=turn_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            summary=summary,
        )
        turn.entries.append(entry)

        # 落盘
        if self._log_dir:
            path = self._log_dir / f"{session_id}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")

        return entry

    def finish_turn(self, session_id: str, turn_id: int) -> None:
        """标记 Turn 完成，记录结束时间。"""
        turn = self._get_turn(session_id, turn_id)
        if turn:
            turn.finished_at = datetime.utcnow()

    # ── 读取接口 ──

    def get_turns(self, session_id: str) -> list[Turn]:
        """返回该 session 的所有 Turn（按 turn_id 升序）。"""
        return list(self._turns.get(session_id, []))

    def get_turn(self, session_id: str, turn_id: int) -> Turn | None:
        return self._get_turn(session_id, turn_id)

    def get_latest_turn(self, session_id: str) -> Turn | None:
        turns = self._turns.get(session_id, [])
        return turns[-1] if turns else None

    def get_entries(self, session_id: str, turn_id: int) -> list[TraceEntry]:
        turn = self._get_turn(session_id, turn_id)
        return list(turn.entries) if turn else []

    # ── 内部 ──

    def _get_turn(self, session_id: str, turn_id: int) -> Turn | None:
        for t in self._turns.get(session_id, []):
            if t.turn_id == turn_id:
                return t
        return None


# 全局单例（app_web.py 和 agent 共享同一个实例）
_global_store: TraceStore | None = None


def get_trace_store(log_dir: str | Path | None = "logs/traces") -> TraceStore:
    """获取全局 TraceStore 单例。"""
    global _global_store
    if _global_store is None:
        _global_store = TraceStore(log_dir=log_dir)
    return _global_store
