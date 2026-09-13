"""Agent 主循环测试：mock LLM + mock Executor。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from linuxagent.core.agent import Agent, AgentEvent
from linuxagent.core.context import EnvSnapshot, SessionContext
from linuxagent.core.llm import LLMResponse, ToolCall
from linuxagent.executor.result import ExecResult


def _make_executor(stdout: str = "ok", stderr: str = "", rc: int = 0) -> Mock:
    exec_result = ExecResult(
        command="stub", stdout=stdout, stderr=stderr, return_code=rc, duration_ms=5
    )
    executor = Mock()
    executor.run = Mock(return_value=exec_result)
    executor.is_connected = Mock(return_value=True)
    executor.close = Mock()
    return executor


@pytest.mark.asyncio
async def test_agent_single_tool_then_final() -> None:
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="c1", name="disk_usage", arguments={"path": "/"})],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="根目录使用率 40%", tool_calls=None, finish_reason="stop"),
    ]
    llm = Mock()
    llm.chat = Mock(side_effect=responses)

    executor = _make_executor(stdout="/dev/sda1 50G 20G 30G /")
    context = SessionContext(
        session_id="test", env_snapshot=EnvSnapshot(os_name="Ubuntu", kernel="5.15")
    )
    agent = Agent(llm=llm, executor=executor, context=context, max_steps=4)

    events: list[AgentEvent] = []
    async for ev in agent.chat("查磁盘"):
        events.append(ev)

    types = [e.type for e in events]
    assert "thinking" in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "final"
    assert "根目录" in events[-1].payload["content"]


@pytest.mark.asyncio
async def test_agent_direct_final_without_tool() -> None:
    llm = Mock()
    llm.chat = Mock(
        return_value=LLMResponse(
            content="你好，我是 LinuxAgent。", tool_calls=None, finish_reason="stop"
        )
    )
    executor = _make_executor()
    context = SessionContext(session_id="t2", env_snapshot=EnvSnapshot())
    agent = Agent(llm=llm, executor=executor, context=context, max_steps=2)

    events = [e async for e in agent.chat("你好")]
    assert events[-1].type == "final"
    assert "LinuxAgent" in events[-1].payload["content"]


@pytest.mark.asyncio
async def test_agent_max_steps_exceeded() -> None:
    tc_resp = LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="c_forever", name="disk_usage", arguments={"path": "/"})],
        finish_reason="tool_calls",
    )
    llm = Mock()
    llm.chat = Mock(return_value=tc_resp)
    executor = _make_executor()
    context = SessionContext(session_id="loop", env_snapshot=EnvSnapshot())
    agent = Agent(llm=llm, executor=executor, context=context, max_steps=2)

    events = [e async for e in agent.chat("死循环")]
    assert events[-1].type == "error"
    assert "max_steps_exceeded" in events[-1].payload.get("reason", "")


@pytest.mark.asyncio
async def test_agent_llm_failure_is_reported() -> None:
    llm = Mock()
    llm.chat = Mock(side_effect=RuntimeError("network broken"))
    executor = _make_executor()
    context = SessionContext(session_id="crash", env_snapshot=EnvSnapshot())
    agent = Agent(llm=llm, executor=executor, context=context, max_steps=2)

    events = [e async for e in agent.chat("test")]
    assert events[-1].type == "error"
    assert "LLM failure" in events[-1].payload.get("reason", "")
