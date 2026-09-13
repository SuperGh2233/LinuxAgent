"""UI 层 Mock 测试：SessionPool + web_components + confirm。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from linuxagent.safety.confirm import ConfirmationPrompt, level_emoji
from linuxagent.safety.risk_engine import RiskAssessment
from linuxagent.tools.base import RiskLevel
from linuxagent.ui.session_pool import SessionPool
from linuxagent.ui.web_components import format_risk_card, format_tool_call_badge, format_tool_result


class TestSessionPool:
    def test_create_new_session(self):
        pool = SessionPool(max_size=5, agent_factory=lambda sid: Mock(session_id=sid))
        sid, agent = pool.get_or_create()
        assert sid is not None
        assert pool.size() == 1

    def test_get_existing_session(self):
        pool = SessionPool(max_size=5, agent_factory=lambda sid: Mock(session_id=sid))
        sid1, agent1 = pool.get_or_create()
        sid2, agent2 = pool.get_or_create(sid1)
        assert sid1 == sid2
        assert agent1 is agent2

    def test_lru_eviction(self):
        pool = SessionPool(max_size=3, agent_factory=lambda sid: Mock())
        sids = [pool.get_or_create()[0] for _ in range(4)]
        assert pool.size() == 3
        assert sids[0] not in [pool.get_or_create(s)[0] for s in sids[1:]]

    def test_release_session(self):
        mock_agent = Mock()
        mock_agent.executor = Mock()
        pool = SessionPool(max_size=5, agent_factory=lambda sid: mock_agent)
        sid, _ = pool.get_or_create()
        assert pool.size() == 1
        pool.release(sid)
        assert pool.size() == 0
        mock_agent.executor.close.assert_called_once()


class TestWebComponents:
    def test_format_risk_card_blocked(self):
        assessment = {
            "level": "BLOCKED",
            "reason": "危险命令",
            "rules": ["rule.rm_root"],
            "suggestions": ["改为安全路径"],
        }
        card = format_risk_card(assessment)
        assert "BLOCKED" in card
        assert "rule.rm_root" in card
        assert "🟥" in card

    def test_format_risk_card_high(self):
        card = format_risk_card({"level": "HIGH", "reason": "test", "rules": [], "suggestions": []})
        assert "HIGH" in card
        assert "🟧" in card

    def test_format_tool_call_badge_short(self):
        badge = format_tool_call_badge("disk_usage", {"path": "/", "human_readable": True})
        assert "disk_usage" in badge
        assert "path" in badge

    def test_format_tool_call_badge_many_args(self):
        args = {f"arg{i}": i for i in range(6)}
        badge = format_tool_call_badge("big_tool", args)
        assert "..." in badge

    def test_format_tool_result_success(self):
        result = {
            "tool_name": "disk_usage",
            "success": True,
            "content": "Filesystem Size...",
        }
        out = format_tool_result(result)
        assert "✅" in out
        assert "disk_usage" in out

    def test_format_tool_result_failure(self):
        result = {
            "tool_name": "user_delete",
            "success": False,
            "content": "userdel: user 'x' not found",
        }
        out = format_tool_result(result)
        assert "❌" in out

    def test_format_tool_result_long_content_truncated(self):
        result = {
            "tool_name": "file_read_tail",
            "success": True,
            "content": "x" * 2000,
        }
        out = format_tool_result(result)
        assert "截断" in out


class TestConfirmationPrompt:
    def _make_prompt(self, level: RiskLevel = RiskLevel.HIGH) -> ConfirmationPrompt:
        assessment = RiskAssessment(
            level=level,
            reason="测试原因",
            rules=["rule.shutdown"],
            suggestions=["先停服务"],
            requires_confirmation=(level == RiskLevel.HIGH),
        )
        return ConfirmationPrompt(
            tool_call_id="c123",
            tool_name="service_restart",
            arguments={"service": "nginx"},
            rendered_cmd="systemctl restart nginx",
            assessment=assessment,
        )

    def test_to_markdown_contains_level(self):
        cp = self._make_prompt()
        md = cp.to_markdown()
        assert "HIGH" in md
        assert "service_restart" in md
        assert "systemctl restart nginx" in md

    def test_level_emoji_mapping(self):
        assert level_emoji("BLOCKED") == "🟥"
        assert level_emoji("HIGH") == "🟧"
        assert level_emoji("MEDIUM") == "🟨"
        assert level_emoji("LOW") == "🟩"
        assert level_emoji("UNKNOWN") == "⬜"
