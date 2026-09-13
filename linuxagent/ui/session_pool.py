"""多浏览器标签会话隔离池（LRU，上限 20）。

每个 session_id 对应一个独立的 Agent 实例，包含独立的：
- SSH 连接
- 对话历史
- 待确认工具调用状态
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from loguru import logger


class SessionPool:
    """线程安全的 Agent 会话 LRU 池。

    Args:
        max_size: 最多容纳的会话数。
        ttl_seconds: 会话最大空闲时间（秒），超时后被驱逐并关闭 SSH。
        agent_factory: ``(session_id) -> Agent`` 可调用，用于懒创建。
    """

    def __init__(
        self,
        max_size: int = 20,
        ttl_seconds: int = 1800,
        agent_factory: Any = None,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._agent_factory = agent_factory
        self._pool: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None = None) -> tuple[str, Any]:
        """获取现有会话或按需创建新会话。

        Args:
            session_id: 传入已有 ID 则续用；None 则分配新 ID。

        Returns:
            ``(session_id, agent)`` 元组。
        """
        with self._lock:
            self._evict_expired()
            if session_id and session_id in self._pool:
                self._pool.move_to_end(session_id)
                self._pool[session_id]["last_active"] = time.monotonic()
                return session_id, self._pool[session_id]["agent"]

            new_id = session_id or uuid.uuid4().hex[:8]
            if self._agent_factory is not None:
                agent = self._agent_factory(new_id)
            else:
                agent = None
            self._pool[new_id] = {"agent": agent, "last_active": time.monotonic()}
            if len(self._pool) > self._max_size:
                evicted_id, evicted = self._pool.popitem(last=False)
                self._close_agent(evicted["agent"])
                logger.info("session_pool: evicted(lru) session={}", evicted_id)
            logger.info("session_pool: created session={} total={}", new_id, len(self._pool))
            return new_id, agent

    def release(self, session_id: str) -> None:
        """主动释放并关闭指定会话。"""
        with self._lock:
            entry = self._pool.pop(session_id, None)
            if entry:
                self._close_agent(entry["agent"])
                logger.info("session_pool: released session={}", session_id)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, e in self._pool.items() if now - e["last_active"] > self._ttl]
        for sid in expired:
            entry = self._pool.pop(sid)
            self._close_agent(entry["agent"])
            logger.info("session_pool: expired session={}", sid)

    @staticmethod
    def _close_agent(agent: Any) -> None:
        if agent is None:
            return
        try:
            executor = getattr(agent, "executor", None)
            if executor is not None:
                executor.close()
        except Exception as e:
            logger.warning("failed to close agent executor: {}", e)

    def size(self) -> int:
        with self._lock:
            return len(self._pool)
