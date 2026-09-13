"""Rich CLI 组件：面板、进度条、风险卡片、确认提示。"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from linuxagent.safety.confirm import level_emoji

console = Console()

_LEVEL_COLOR: dict[str, str] = {
    "BLOCKED": "red",
    "HIGH": "dark_orange",
    "MEDIUM": "yellow",
    "LOW": "green",
}


def print_welcome() -> None:
    console.print(
        Panel(
            "[bold cyan]LinuxAgent[/] — 自然语言驱动 Linux 运维助手\n"
            "[dim]输入 'exit' / 'quit' 退出，'history' 查看对话记录[/]",
            title="🤖 Welcome",
            border_style="cyan",
        )
    )


def print_thinking(step: int) -> None:
    console.print(f"[dim cyan]🔄 ReAct 步骤 {step + 1} 中...[/]")


def print_tool_call(name: str, arguments: dict[str, Any]) -> None:
    args_brief = ", ".join(f"{k}={v!r}" for k, v in list(arguments.items())[:4])
    console.print(f"  [bold blue]🔧 调用工具:[/] [cyan]{name}[/]({args_brief})")


def print_tool_result(tool_name: str, success: bool, content: str) -> None:
    icon = "✅" if success else "❌"
    color = "green" if success else "red"
    console.print(f"  [{color}]{icon} {tool_name}[/]")
    if content and len(content) > 800:
        content = content[:800] + "\n... [截断] ..."
    if content:
        console.print(Panel(content, border_style=color, padding=(0, 1)))


def print_risk_warning(assessment: dict[str, Any]) -> None:
    level = assessment.get("level", "LOW")
    color = _LEVEL_COLOR.get(level, "white")
    emoji = level_emoji(level)
    reason = assessment.get("reason", "")
    console.print(
        Panel(
            f"[bold]判定依据：[/]{reason}\n"
            + ("命中规则：" + "、".join(assessment.get("rules", [])) if assessment.get("rules") else ""),
            title=f"{emoji} [bold {color}]风险警告：{level}[/]",
            border_style=color,
        )
    )


def print_confirmation_prompt(
    tool_name: str,
    arguments: dict[str, Any],
    assessment: dict[str, Any],
    rendered_cmd: str,
) -> None:
    level = assessment.get("level", "HIGH")
    color = _LEVEL_COLOR.get(level, "dark_orange")
    emoji = level_emoji(level)
    reason = assessment.get("reason", "")
    suggestions = assessment.get("suggestions", [])

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold")
    table.add_column("val")
    table.add_row("工具", f"[cyan]{tool_name}[/]")
    table.add_row("风险级别", f"[bold {color}]{emoji} {level}[/]")
    table.add_row("判定依据", reason)
    table.add_row("近似命令", f"[dim]{rendered_cmd}[/]")
    if suggestions:
        table.add_row("规避建议", "\n".join(f"• {s}" for s in suggestions))

    args_json = json.dumps(arguments, ensure_ascii=False, indent=2)
    console.print(
        Panel(
            table,
            title=f"⏸️  [bold {color}]高风险操作 — 请确认[/]",
            border_style=color,
        )
    )
    console.print(Panel(args_json, title="参数详情", border_style="dim"))


def ask_confirmation() -> bool:
    """交互式确认提示，返回 True 表示同意执行。"""
    from rich.prompt import Prompt

    answer = Prompt.ask(
        "[bold yellow]是否继续执行？[/] [dim](y=同意 / n=拒绝)[/]",
        choices=["y", "n"],
        default="n",
    )
    return answer.lower() == "y"


def print_final(content: str) -> None:
    console.print(Panel(Markdown(content), title="💬 LinuxAgent", border_style="green"))


def print_error(reason: str) -> None:
    console.print(Panel(f"[bold red]错误[/]: {reason}", border_style="red"))
