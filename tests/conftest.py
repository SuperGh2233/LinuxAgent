"""pytest fixtures。"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from linuxagent.executor.ssh_executor import SSHExecutor


@pytest.fixture(scope="session")
def ssh_executor() -> Iterator[SSHExecutor]:
    """真实 SSH 执行器 fixture。

    未通过环境变量提供 SSH 凭证时自动 skip。
    支持的环境变量：
    - ``SSH_HOST`` / ``SSH_PORT`` / ``SSH_USER`` / ``SSH_PASSWORD``
    - 或带 ``LINUXAGENT_`` 前缀的版本
    """
    host = os.getenv("SSH_HOST") or os.getenv("LINUXAGENT_SSH_HOST")
    user = os.getenv("SSH_USER") or os.getenv("LINUXAGENT_SSH_USER") or "root"
    password = os.getenv("SSH_PASSWORD") or os.getenv("LINUXAGENT_SSH_PASSWORD")
    port = int(os.getenv("SSH_PORT") or os.getenv("LINUXAGENT_SSH_PORT") or "22")

    if not host or not password:
        pytest.skip("SSH env vars (SSH_HOST/SSH_PASSWORD) not configured")

    executor = SSHExecutor(host=host, port=port, username=user, password=password, timeout=30)
    executor.connect()
    try:
        yield executor
    finally:
        executor.close()
