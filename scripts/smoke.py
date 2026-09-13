"""冒烟脚本：一句话驱动 LinuxAgent 跑通 LLM + SSH。

用法:

    python -m scripts.smoke --ask "查一下根目录磁盘占用"
    python -m scripts.smoke --ask "把 tmp 用户删掉" --auto-reject
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger

from linuxagent.config import get_settings
from linuxagent.core.agent import AgentEvent, build_agent
from linuxagent.utils.logger import setup_logging


async def _run(user_ask: str, auto_reject: bool) -> int:
    settings = get_settings()
    setup_logging(
        level=settings.logging.level,
        log_file=settings.logging.file,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
    )
    agent = build_agent()
    try:
        async for ev in agent.chat(user_ask):
            _print_event(ev)
            if ev.type == "confirmation_required":
                if auto_reject:
                    logger.info("auto-reject pending confirmation")
                    async for ev2 in agent.resume(approved=False, user_comment="auto-reject smoke"):
                        _print_event(ev2)
                else:
                    print("[Paused] pending confirmation; pass --auto-reject to decline")
                    break
    finally:
        agent.executor.close()
    return 0


def _print_event(ev: AgentEvent) -> None:
    payload = json.dumps(ev.payload, ensure_ascii=False, default=str)
    if len(payload) > 400:
        payload = payload[:400] + "...[truncated]"
    print(f"[{ev.type}] {payload}")


def main() -> int:
    parser = argparse.ArgumentParser(description="LinuxAgent smoke test")
    parser.add_argument("--ask", required=True, help="用户自然语言请求")
    parser.add_argument(
        "--auto-reject",
        action="store_true",
        help="若出现 HIGH 风险确认，自动拒绝，不阻塞脚本",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.ask, args.auto_reject))


if __name__ == "__main__":
    sys.exit(main())
