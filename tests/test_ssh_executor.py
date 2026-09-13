"""SSHExecutor 真实连通性测试。

需通过环境变量配置 SSH_HOST / SSH_PASSWORD 才会运行。
"""

from __future__ import annotations

from linuxagent.executor.ssh_executor import SSHExecutor


def test_connect_and_echo(ssh_executor: SSHExecutor) -> None:
    result = ssh_executor.run("echo hello")
    assert result.return_code == 0
    assert "hello" in result.stdout
    assert result.success is True


def test_run_df_h(ssh_executor: SSHExecutor) -> None:
    result = ssh_executor.run("df -h")
    assert result.return_code == 0
    assert "Filesystem" in result.stdout or "文件系统" in result.stdout


def test_run_invalid_command(ssh_executor: SSHExecutor) -> None:
    result = ssh_executor.run("this_command_does_not_exist_12345")
    assert result.return_code != 0
    assert result.stderr or result.stdout


def test_truncate_large_output(ssh_executor: SSHExecutor) -> None:
    result = ssh_executor.run("yes | head -c 20000")
    assert result.return_code == 0
    assert result.truncated is True
