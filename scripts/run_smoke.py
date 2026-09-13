"""直接运行冒烟测试（绕过命令行中文编码问题）。"""
import asyncio
import sys
import os

# 设置 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linuxagent.config import get_settings
from linuxagent.core.agent import AgentEvent, build_agent
from linuxagent.utils.logger import setup_logging
import json


async def main():
    settings = get_settings()
    setup_logging(level="INFO", log_file=settings.logging.file)

    print("正在连接服务器并初始化 Agent...")
    agent = build_agent()
    print(f"SSH 连接成功！服务器: {settings.ssh.host}")
    print(f"环境探测: OS={agent.context.env_snapshot.os_name} "
          f"内核={agent.context.env_snapshot.kernel} "
          f"架构={agent.context.env_snapshot.arch}")
    print("-" * 60)

    questions = [
        "查看根目录磁盘使用情况",
        "列出 CPU 占用最高的 5 个进程",
    ]

    for q in questions:
        print(f"\n>>> 用户: {q}")
        async for ev in agent.chat(q):
            if ev.type == "thinking":
                print(f"  [思考中... 步骤{ev.payload.get('step',0)+1}]")
            elif ev.type == "tool_call":
                print(f"  [调用工具] {ev.payload.get('name')}({json.dumps(ev.payload.get('arguments',{}), ensure_ascii=False)})")
            elif ev.type == "tool_result":
                success = ev.payload.get("success")
                print(f"  [工具结果] {'✓' if success else '✗'} {ev.payload.get('tool_name')}")
            elif ev.type == "risk_warning":
                level = ev.payload.get('level','')
                print(f"  [风险警告] 级别={level} 原因={ev.payload.get('reason','')[:80]}")
            elif ev.type == "confirmation_required":
                print(f"  [需要确认] HIGH 风险已挂起，smoke 脚本自动跳过")
                # 自动拒绝
                async for ev2 in agent.resume(approved=False, user_comment="smoke auto-reject"):
                    if ev2.type == "final":
                        print(f"\n<<< LinuxAgent 回复:\n{ev2.payload.get('content','')}")
                break
            elif ev.type == "final":
                print(f"\n<<< LinuxAgent 回复:\n{ev.payload.get('content','')}")
            elif ev.type == "error":
                print(f"  [错误] {ev.payload.get('reason')}")

    agent.executor.close()
    print("\n\n冒烟测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
