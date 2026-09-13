"""基于 loguru 的全局日志初始化。

- Console handler: INFO+ 彩色输出
- File handler: DEBUG+ 按天轮转
- 提供 ``mask_secret`` 对敏感信息做前后脱敏
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_configured: bool = False


def setup_logging(
    level: str = "INFO",
    log_file: str | Path = "logs/app.log",
    rotation: str = "1 day",
    retention: str = "7 days",
) -> None:
    """初始化全局日志 sink，幂等。

    Args:
        level: 控制台日志级别。
        log_file: 文件日志路径，不存在时自动创建父目录。
        rotation: loguru 支持的轮转策略字符串。
        retention: 日志保留策略字符串。
    """
    global _configured
    if _configured:
        return

    logger.remove()

    console_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level=level, format=console_fmt, colorize=True, enqueue=False)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_path,
        level="DEBUG",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    _configured = True
    logger.debug("logging configured: level={} file={}", level, log_path)


def mask_secret(value: str | None, keep: int = 4) -> str:
    """对敏感字符串做前后保留脱敏。

    Args:
        value: 待脱敏字符串，None 或空串直接返回 ``***``。
        keep: 前后各保留的字符数。

    Returns:
        形如 ``sk-a***...***abcd`` 的脱敏字符串。
    """
    if not value:
        return "***"
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...{value[-keep:]}"
