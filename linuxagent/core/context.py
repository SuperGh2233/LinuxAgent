"""会话上下文：对话历史、环境快照、审计记录。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EnvSnapshot(BaseModel):
    """目标服务器环境快照。"""

    os_name: str = "unknown"
    os_version: str = ""
    kernel: str = ""
    arch: str = ""
    pkg_mgr: str = ""
    has_systemd: bool = False
    probed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AuditEntry(BaseModel):
    """审计日志条目。"""

    ts: datetime = Field(default_factory=datetime.utcnow)
    kind: Literal[
        "user", "llm", "tool_call", "tool_result",
        "risk", "final", "error", "confirm",
    ]
    payload: dict[str, Any]


class SessionContext(BaseModel):
    """单会话的状态容器。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    env_snapshot: EnvSnapshot = Field(default_factory=EnvSnapshot)
    audit_log: list[AuditEntry] = Field(default_factory=list)
    pending_tool_call: dict[str, Any] | None = None
    approved_tool_call_ids: set[str] = Field(default_factory=set)

    def append_user(self, content: str) -> None:
        self.history.append({"role": "user", "content": content})
        self.audit_log.append(AuditEntry(kind="user", payload={"content": content}))

    def append_assistant(
        self,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.history.append(msg)
        self.audit_log.append(AuditEntry(kind="llm", payload=msg))

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }
        self.history.append(msg)
        self.audit_log.append(AuditEntry(kind="tool_result", payload=msg))

    def render_messages(self, system_prompt: str, env_brief: str) -> list[dict[str, Any]]:
        """组装 OpenAI 格式 messages。"""
        return [
            {"role": "system", "content": system_prompt + "\n\n" + env_brief},
            *self.history,
        ]

    def trim(self, max_turns: int = 20) -> None:
        """按用户轮次滚动裁剪 history，避免切断 tool_call 对齐。"""
        if not self.history:
            return
        user_count = 0
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i].get("role") == "user":
                user_count += 1
                if user_count >= max_turns:
                    self.history = self.history[i:]
                    return

    def is_approved(self, tool_call_id: str) -> bool:
        return tool_call_id in self.approved_tool_call_ids

    def approve(self, tool_call_id: str) -> None:
        self.approved_tool_call_ids.add(tool_call_id)

    def repair_orphan_tool_calls(self) -> int:
        """修复 history 中悬空的 tool_calls：为每个没有对应 tool_result 的 tool_call
        插入一条占位 tool 消息，防止 LLM API 返回 400 错误。

        Returns:
            修复的 tool_call 数量（0 表示 history 本来就合法）。
        """
        repaired = 0
        i = 0
        while i < len(self.history):
            msg = self.history[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # 收集该 assistant 消息声明的所有 tool_call_id
                declared: list[dict] = [
                    tc for tc in msg["tool_calls"] if isinstance(tc, dict) and tc.get("id")
                ]
                # 找紧随其后的 tool 消息，收集已覆盖的 id
                j = i + 1
                covered: set[str] = set()
                while j < len(self.history) and self.history[j].get("role") == "tool":
                    covered.add(self.history[j].get("tool_call_id", ""))
                    j += 1
                # 为缺失的 tool_call_id 插入占位消息（按声明顺序）
                insert_at = j  # 在已有 tool 消息之后插入
                for tc in declared:
                    tc_id = tc["id"]
                    if tc_id not in covered:
                        tc_name = tc.get("function", {}).get("name", "unknown")
                        placeholder = {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": tc_name,
                            "content": "[操作已取消或中断，请根据实际情况继续]",
                        }
                        self.history.insert(insert_at, placeholder)
                        insert_at += 1
                        repaired += 1
                # 跳过已处理的 tool 消息段
                i = insert_at
            else:
                i += 1
        return repaired
