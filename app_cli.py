"""Rich + prompt_toolkit CLI 入口。

启动：
    python app_cli.py [--no-safety]

特性：
- prompt_toolkit 多行输入 + 历史文件（.linuxagent_history）
- Rich 彩色输出：思考步骤 / 工具调用 / 风险卡片 / 最终回答
- HIGH 风险交互确认（控制台二次确认）
- 输入 exit/quit 优雅退出
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from linuxagent.config import get_settings
from linuxagent.core.agent import Agent, AgentEvent, build_agent
from linuxagent.ui.cli_components import (
    ask_confirmation,
    console,
    print_confirmation_prompt,
    print_error,
    print_final,
    print_risk_warning,
    print_thinking,
    print_tool_call,
    print_tool_result,
    print_welcome,
)
from linuxagent.utils.helpers import render_command_preview
from linuxagent.utils.logger import setup_logging


async def _run_one_turn(
    agent: Agent,
    user_input: str,
) -> None:
    """执行一轮对话，处理确认事件。"""
    pending_approval: dict | None = None

    async for ev in agent.chat(user_input):
        _handle_event(ev)
        if ev.type == "confirmation_required":
            tc = ev.payload.get("tool_call", {})
            assessment = ev.payload.get("assessment", {})
            rendered = render_command_preview(tc.get("name", "?"), tc.get("arguments", {}))
            print_confirmation_prompt(
                tool_name=tc.get("name", "?"),
                arguments=tc.get("arguments", {}),
                assessment=assessment,
                rendered_cmd=rendered,
            )
            approved = ask_confirmation()
            pending_approval = {"approved": approved}
            break

    if pending_approval is not None:
        async for ev in agent.resume(
            approved=pending_approval["approved"],
            user_comment="CLI 用户手动确认" if pending_approval["approved"] else "CLI 用户拒绝",
        ):
            _handle_event(ev)


def _handle_event(ev: AgentEvent) -> None:
    if ev.type == "thinking":
        print_thinking(ev.payload.get("step", 0))
    elif ev.type == "tool_call":
        print_tool_call(ev.payload.get("name", "?"), ev.payload.get("arguments", {}))
    elif ev.type == "tool_result":
        print_tool_result(
            tool_name=ev.payload.get("tool_name", "?"),
            success=ev.payload.get("success", False),
            content=ev.payload.get("content", ""),
        )
    elif ev.type == "risk_warning":
        print_risk_warning(ev.payload)
    elif ev.type == "final":
        print_final(ev.payload.get("content", ""))
    elif ev.type == "error":
        print_error(ev.payload.get("reason", "未知错误"))


async def _main_loop(agent: Agent) -> None:
    print_welcome()
    history_file = FileHistory(".linuxagent_history")
    session: PromptSession = PromptSession(history=history_file)

    while True:
        try:
            user_input = await session.prompt_async("🖥️  > ")
        except KeyboardInterrupt:
            console.print("[dim]\nCtrl+C — 再次按下退出[/]")
            try:
                await session.prompt_async("")
            except (KeyboardInterrupt, EOFError):
                break
            continue
        except EOFError:
            break

        stripped = user_input.strip()
        if not stripped:
            continue
        if stripped.lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Bye![/]")
            break
        if stripped.lower() == "history":
            _show_history(agent)
            continue

        try:
            await _run_one_turn(agent, stripped)
        except Exception as e:
            print_error(f"意外错误: {e}")
            logger.exception("CLI error")


def _show_history(agent: Agent) -> None:
    console.print(f"[dim]当前对话轮数：{len(agent.context.history)}[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="LinuxAgent CLI")
    parser.add_argument("--no-safety", action="store_true", help="禁用风险引擎（调试用）")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(
        level=settings.logging.level,
        log_file=settings.logging.file,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
    )
    logger.info("Starting LinuxAgent CLI safety={}", not args.no_safety)

    agent = build_agent(enable_safety=not args.no_safety)
    try:
        asyncio.run(_main_loop(agent))
    finally:
        agent.executor.close()
        logger.info("CLI exited.")


if __name__ == "__main__":
    main()
