"""工具注册与调用测试。全部基于 mock executor，不依赖真实 SSH。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from linuxagent.executor.result import ExecResult
from linuxagent.tools import invoke_tool, list_tool_names, openai_tools_schema
from linuxagent.tools.base import ToolContext


def _make_ctx(stdout: str = "ok", stderr: str = "", rc: int = 0) -> ToolContext:
    exec_result = ExecResult(
        command="",
        stdout=stdout,
        stderr=stderr,
        return_code=rc,
        duration_ms=1,
    )
    mock_exec = Mock()
    mock_exec.run = Mock(return_value=exec_result)
    return ToolContext(executor=mock_exec, session_id="test-session", env_snapshot={})


def test_auto_registration_covers_all_categories() -> None:
    names = set(list_tool_names())
    expected = {
        "system_info", "system_load",
        "disk_usage", "disk_inode",
        "file_search", "file_read_tail", "file_stat",
        "process_list", "port_check", "service_status",
        "user_list", "user_create", "user_delete",
        "network_ping", "network_route", "network_interfaces",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_tool_count_at_least_13() -> None:
    assert len(list_tool_names()) >= 13


def test_openai_tools_schema_shape() -> None:
    schemas = openai_tools_schema()
    assert len(schemas) >= 13
    for s in schemas:
        assert s["type"] == "function"
        fn = s["function"]
        assert {"name", "description", "parameters"}.issubset(fn.keys())
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params


def test_invoke_unknown_tool_returns_error() -> None:
    ctx = _make_ctx()
    result = invoke_tool("no_such_tool", {}, ctx)
    assert result.success is False
    assert result.error is not None
    assert "not found" in result.error


def test_invoke_disk_usage_ok() -> None:
    ctx = _make_ctx(stdout="Filesystem  Size Used Avail\n/dev/sda1 50G 20G 30G /")
    result = invoke_tool("disk_usage", {"path": "/"}, ctx)
    assert result.success is True
    assert result.tool_name == "disk_usage"


def test_invoke_disk_usage_default_path() -> None:
    ctx = _make_ctx(stdout="...")
    result = invoke_tool("disk_usage", {}, ctx)
    assert result.success is True


def test_invoke_port_check_invalid_port_rejected_by_tool() -> None:
    ctx = _make_ctx()
    result = invoke_tool("port_check", {"port": 99999}, ctx)
    assert result.success is False
    assert result.error and "Invalid port" in result.error


def test_invoke_user_create_invalid_username() -> None:
    ctx = _make_ctx()
    result = invoke_tool("user_create", {"username": "bad name!!"}, ctx)
    assert result.success is False
    assert result.error and "Invalid username" in result.error


def test_invoke_user_delete_root_blocked() -> None:
    ctx = _make_ctx()
    result = invoke_tool("user_delete", {"username": "root", "remove_home": True}, ctx)
    assert result.success is False
    assert result.error and "Refuse to delete" in result.error


def test_invoke_process_list_sort_mem() -> None:
    ctx = _make_ctx(stdout="USER PID %CPU %MEM ...")
    result = invoke_tool("process_list", {"sort_by": "mem", "top_n": 5}, ctx)
    assert result.success is True


def test_invoke_file_search_content_mode() -> None:
    ctx = _make_ctx(stdout="/var/log/syslog:12: error: hello")
    result = invoke_tool(
        "file_search",
        {"path": "/var/log", "pattern": "error", "search_type": "content"},
        ctx,
    )
    assert result.success is True


def test_invalid_arguments_get_validation_error() -> None:
    ctx = _make_ctx()
    # port 是 int，传 str 应失败
    result = invoke_tool("port_check", {"port": "not a number"}, ctx)
    assert result.success is False
    assert result.error and "Invalid arguments" in result.error


def test_tool_schema_literal_field_has_enum() -> None:
    schemas = openai_tools_schema()
    pl = next(s for s in schemas if s["function"]["name"] == "process_list")
    sort_by = pl["function"]["parameters"]["properties"]["sort_by"]
    assert "enum" in sort_by
    assert set(sort_by["enum"]) == {"cpu", "mem"}
