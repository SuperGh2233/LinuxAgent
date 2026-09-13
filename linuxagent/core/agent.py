"""Agent 主循环：ReAct 风格 + Function Calling + 事件流。

M2 阶段不带风险引擎；M3 通过构造器注入 ``risk_engine`` 自动启用三闸检查。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from linuxagent.config import Settings, get_settings
from linuxagent.core.context import EnvSnapshot, SessionContext
from linuxagent.core.llm import LLMGateway, ToolCall, build_llm_gateway
from linuxagent.core.prompts import SYSTEM_PROMPT_ZH, render_env_brief
from linuxagent.executor.ssh_executor import SSHExecutor
from linuxagent.safety.intent_filter import check_intent
from linuxagent.tools import get_tool, invoke_tool, openai_tools_schema
from linuxagent.tools.base import RiskLevel, ToolContext
from linuxagent.tools.system import probe_env


class AgentEvent(BaseModel):
    """Agent 对外产出的事件。"""

    type: Literal[
        "thinking",
        "tool_call",
        "tool_result",
        "risk_warning",
        "confirmation_required",
        "plan_created",
        "plan_updated",
        "final",
        "error",
        "intent_blocked",   # 意图级拦截，在 LLM 规划前触发
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=datetime.utcnow)


class Agent:
    """ReAct 主循环 Agent。"""

    def __init__(
        self,
        llm: LLMGateway,
        executor: SSHExecutor,
        context: SessionContext,
        max_steps: int = 8,
        system_prompt: str = SYSTEM_PROMPT_ZH,
        risk_engine: Any = None,
    ) -> None:
        """初始化 Agent。

        Args:
            llm: LLM 网关。
            executor: SSH 执行器。
            context: 会话上下文。
            max_steps: 单次 ``chat`` 内最多循环的 ReAct 轮数。
            system_prompt: 系统提示词。
            risk_engine: 可选的风险引擎（M3 注入）；为 None 时跳过三闸检查。
        """
        self.llm = llm
        self.executor = executor
        self.context = context
        self.max_steps = max_steps
        self.system_prompt = system_prompt
        self.risk_engine = risk_engine
        self._current_plan: Any = None
        self._current_turn_id: int = 0
        # 懒加载 TraceStore，避免循环导入
        self._trace_store: Any = None
        self._tool_context = ToolContext(
            executor=executor,
            session_id=context.session_id,
            env_snapshot=context.env_snapshot.as_dict(),
        )

    def _get_trace_store(self) -> Any:
        """懒加载全局 TraceStore。"""
        if self._trace_store is None:
            from linuxagent.core.trace_store import get_trace_store
            self._trace_store = get_trace_store()
        return self._trace_store

    def _trace(self, ev: AgentEvent) -> None:
        """把 AgentEvent 写入 TraceStore（静默失败，不影响主流程）。"""
        if self._current_turn_id == 0:
            return
        try:
            self._get_trace_store().append(
                session_id=self.context.session_id,
                turn_id=self._current_turn_id,
                event_type=ev.type,
                payload=ev.payload,
            )
        except Exception as e:
            logger.debug("trace append failed (non-fatal): {}", e)

    async def chat(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """用户输入一句话，驱动一轮多步 ReAct 循环（含 Plan-Act 规划阶段）。"""

        # ── 意图级拦截：在 LLM 规划前检测自然语言意图 ────────────────
        intent_block = check_intent(user_message)
        if intent_block:
            logger.info(
                "intent blocked by rule={} msg={!r}", intent_block.rule_id, user_message[:80]
            )
            block_msg = (
                f"🚫 **操作被安全系统直接拦截**\n\n"
                f"**规则**：{intent_block.description}\n\n"
                f"**原因**：{intent_block.reason}\n\n"
                + (
                    "**建议**：" + " · ".join(intent_block.suggestions) + "\n"
                    if intent_block.suggestions
                    else ""
                )
            )
            # 不写入对话历史，避免污染上下文
            yield AgentEvent(
                type="intent_blocked",
                payload={
                    "rule_id": intent_block.rule_id,
                    "description": intent_block.description,
                    "reason": intent_block.reason,
                    "suggestions": intent_block.suggestions,
                    "message": block_msg,
                },
            )
            return

        self.context.append_user(user_message)

        # 创建新 Turn
        try:
            store = self._get_trace_store()
            self._current_turn_id = store.new_turn(
                self.context.session_id, user_message
            )
        except Exception as e:
            logger.debug("trace new_turn failed: {}", e)

        # ── Plan 阶段：生成执行计划（在独立线程中避免阻塞事件循环）──
        try:
            from linuxagent.core.planner import make_plan

            plan = await asyncio.to_thread(make_plan, self.llm, user_message)
            self._current_plan = plan
            ev = AgentEvent(
                type="plan_created",
                payload={"plan": plan.model_dump()},
            )
            self._trace(ev)
            yield ev
        except Exception as e:
            logger.warning("Plan generation failed, continuing without plan: {}", e)
            self._current_plan = None

        try:
            async for ev in self._loop():
                self._trace(ev)
                yield ev
        except Exception as e:
            logger.exception("agent loop crashed")
            err_ev = AgentEvent(type="error", payload={"reason": f"agent crashed: {e}"})
            self._trace(err_ev)
            yield err_ev
        finally:
            try:
                self._get_trace_store().finish_turn(
                    self.context.session_id, self._current_turn_id
                )
            except Exception:
                pass

    async def resume(
        self,
        approved: bool,
        user_comment: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """HIGH 风险被挂起后，根据用户决定继续主循环。

        Args:
            approved: 用户是否同意执行。
            user_comment: 可选的用户备注，会被写入 tool 消息供 LLM 理解。
        """
        pending = self.context.pending_tool_call
        if not pending:
            yield AgentEvent(type="error", payload={"reason": "no pending confirmation"})
            return

        tool_call_id: str = pending["id"]
        tool_name: str = pending["name"]
        arguments: dict[str, Any] = pending["arguments"]
        self.context.pending_tool_call = None

        if not approved:
            refuse_msg = f"[用户拒绝执行] {user_comment or '(无备注)'}"
            self.context.append_tool_result(tool_call_id, tool_name, refuse_msg)
            ev = AgentEvent(
                type="tool_result",
                payload={
                    "tool_name": tool_name,
                    "success": False,
                    "content": refuse_msg,
                    "data": None,
                    "error": "user_rejected",
                },
            )
            self._trace(ev)
            yield ev
        else:
            self.context.approve(tool_call_id)
            tc = ToolCall(id=tool_call_id, name=tool_name, arguments=arguments)
            async for ev in self._invoke_single(tc):
                self._trace(ev)
                yield ev

        async for ev in self._loop():
            self._trace(ev)
            yield ev

    async def _loop(self) -> AsyncIterator[AgentEvent]:
        env_brief = render_env_brief(
            self.context.env_snapshot.as_dict(), self.context.session_id
        )

        # 修复可能存在的悬空 tool_calls（confirmation_required 中断时留下的）
        n_repaired = self.context.repair_orphan_tool_calls()
        if n_repaired:
            logger.warning("Repaired {} orphan tool_call(s) before entering loop", n_repaired)

        # 将计划信息注入到系统提示（只在有计划时）
        effective_system = self.system_prompt
        if self._current_plan is not None:
            plan_hint = (
                "\n\n---\n**【当前执行计划】**\n"
                + self._current_plan.render_markdown()
                + "\n请按照上述计划顺序执行，完成后给出总结。"
            )
            effective_system = self.system_prompt + plan_hint

        for step in range(self.max_steps):
            self.context.trim(max_turns=20)
            messages = self.context.render_messages(effective_system, env_brief)
            tools_schema = openai_tools_schema()

            yield AgentEvent(type="thinking", payload={"step": step})

            try:
                resp = await asyncio.to_thread(
                    self.llm.chat, messages, tools_schema, "auto"
                )
            except Exception as e:
                logger.exception("LLM call failed in agent loop")
                yield AgentEvent(type="error", payload={"reason": f"LLM failure: {e}"})
                return

            if resp.tool_calls:
                tool_calls_payload = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _json_args(tc.arguments),
                        },
                    }
                    for tc in resp.tool_calls
                ]
                self.context.append_assistant(resp.content, tool_calls=tool_calls_payload)

                stopped_for_confirmation = False
                for tc in resp.tool_calls:
                    async for ev in self._invoke_single(tc):
                        yield ev
                        if ev.type == "confirmation_required":
                            stopped_for_confirmation = True
                    if stopped_for_confirmation:
                        return

                # 计划已完成：强制收尾总结，避免继续无意义工具调用直到 max_steps_exceeded
                if self._current_plan is not None and self._current_plan.is_finished():
                    final_prompt = (
                        "计划步骤已完成。请基于已有工具结果给出最终结论，"
                        "不要再调用任何工具。用简洁中文回答。"
                    )
                    self.context.trim(max_turns=20)
                    final_messages = self.context.render_messages(effective_system, env_brief) + [
                        {"role": "user", "content": final_prompt}
                    ]
                    try:
                        final_resp = await asyncio.to_thread(
                            self.llm.chat, final_messages, tools_schema, "none"
                        )
                        final_content = final_resp.content or "(任务完成)"
                    except Exception as e:
                        logger.warning("forced final summary failed, fallback local text: {}", e)
                        final_content = "任务已执行完成，但总结阶段出现异常。请查看上方工具结果。"
                    self.context.append_assistant(final_content)
                    yield AgentEvent(type="final", payload={"content": final_content})
                    return
            else:
                content = resp.content or "(空回复)"
                self.context.append_assistant(content)
                # 完成最后一个 running 步骤，剩余 pending 标为 skipped
                if self._current_plan is not None:
                    self._current_plan.complete_current("汇总完成")
                    self._current_plan.mark_remaining_skipped()
                    yield AgentEvent(
                        type="plan_updated",
                        payload={"plan": self._current_plan.model_dump()},
                    )
                yield AgentEvent(type="final", payload={"content": content})
                return

        yield AgentEvent(type="error", payload={"reason": "max_steps_exceeded"})

    async def _invoke_single(self, tc: ToolCall) -> AsyncIterator[AgentEvent]:
        """执行单个 ToolCall：先走三闸，再调执行，同步更新计划状态。"""
        yield AgentEvent(
            type="tool_call",
            payload={"id": tc.id, "name": tc.name, "arguments": tc.arguments},
        )

        if self.risk_engine is not None and not self.context.is_approved(tc.id):
            from linuxagent.utils.helpers import render_command_preview

            rendered = render_command_preview(tc.name, tc.arguments)
            tool_def = get_tool(tc.name)
            tool_hint = tool_def.risk_hint if tool_def else RiskLevel.MEDIUM

            try:
                assessment = await asyncio.to_thread(
                    self.risk_engine.assess,
                    tc.name,
                    tc.arguments,
                    rendered,
                    tool_hint,
                )
            except Exception as e:
                logger.exception("risk engine raised; skipping risk gate once")
                assessment = None

            if assessment is not None:
                yield AgentEvent(
                    type="risk_warning",
                    payload=assessment.model_dump(mode="json"),
                )
                if assessment.level == RiskLevel.BLOCKED:
                    refuse = (
                        f"[BLOCKED] {assessment.reason}. "
                        f"建议：{'; '.join(assessment.suggestions) if assessment.suggestions else '无'}"
                    )
                    self.context.append_tool_result(tc.id, tc.name, refuse)
                    yield AgentEvent(
                        type="tool_result",
                        payload={
                            "tool_name": tc.name,
                            "success": False,
                            "content": refuse,
                            "data": None,
                            "error": "blocked_by_safety",
                        },
                    )
                    return
                if assessment.level == RiskLevel.HIGH and assessment.requires_confirmation:
                    self.context.pending_tool_call = {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "assessment": assessment.model_dump(mode="json"),
                    }
                    yield AgentEvent(
                        type="confirmation_required",
                        payload={
                            "tool_call": {
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                            "assessment": assessment.model_dump(mode="json"),
                        },
                    )
                    return

        # 风险检查通过后，才推进计划状态（避免 confirmation_required 时推进了但工具未执行）
        if self._current_plan is not None:
            self._current_plan.advance(tc.name)
            yield AgentEvent(
                type="plan_updated",
                payload={"plan": self._current_plan.model_dump()},
            )

        result = await asyncio.to_thread(
            invoke_tool, tc.name, tc.arguments, self._tool_context
        )
        self.context.append_tool_result(tc.id, tc.name, result.content)

        # 完成当前步骤：用结果摘要更新计划
        if self._current_plan is not None:
            brief = (result.content or "")[:120].replace("\n", " ")
            self._current_plan.complete_current(brief)
            yield AgentEvent(
                type="plan_updated",
                payload={"plan": self._current_plan.model_dump()},
            )

        yield AgentEvent(type="tool_result", payload=result.model_dump(mode="json"))


def _json_args(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except Exception:
        return "{}"


def build_agent(
    session_id: str | None = None,
    settings: Settings | None = None,
    risk_engine: Any = None,
    enable_safety: bool = True,
) -> Agent:
    """一站式构造 Agent 实例（建立 SSH、探测环境、构造 LLM、装配风险引擎）。

    Args:
        session_id: 会话 ID；不传则自动生成。
        settings: 配置对象；不传则用全局单例。
        risk_engine: 可选风险引擎实例；传入将覆盖默认装配。
        enable_safety: 是否自动装配默认风险引擎（M3 及以后默认 True）。

    Returns:
        就绪可用的 ``Agent``。
    """
    s = settings or get_settings()
    session_id = session_id or uuid.uuid4().hex[:8]

    password = s.ssh_password.get_secret_value() or None
    key_path = s.ssh.key_path if s.ssh.auth_mode == "key" else None
    executor = SSHExecutor(
        host=s.ssh.host,
        port=s.ssh.port,
        username=s.ssh.username,
        password=password,
        key_path=key_path,
        timeout=s.ssh.default_timeout_s,
    )
    executor.connect()

    env_dict = probe_env(executor)
    env = EnvSnapshot(**{**env_dict, "probed_at": datetime.utcnow()})
    context = SessionContext(session_id=session_id, env_snapshot=env)

    llm = build_llm_gateway(s)

    if risk_engine is None and enable_safety:
        try:
            from linuxagent.safety import build_risk_engine

            risk_engine = build_risk_engine(s, llm)
        except Exception as e:
            logger.warning("build_risk_engine failed, continue without safety gate: {}", e)
            risk_engine = None

    return Agent(
        llm=llm,
        executor=executor,
        context=context,
        max_steps=s.agent.max_steps,
        risk_engine=risk_engine,
    )
