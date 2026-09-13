"""基于 paramiko 的 SSH 执行器。

设计要点：
- 单连接实例，自动重连
- keepalive 防止被服务端踢
- stdout 超阈值自动截断（避免 token 爆炸）
- ``with`` 上下文管理器支持
"""

from __future__ import annotations

import time
from pathlib import Path

import paramiko
from loguru import logger

from linuxagent.executor.result import ExecResult
from linuxagent.utils.errors import SSHConnectionError, SSHExecutionError
from linuxagent.utils.helpers import safe_truncate

_MAX_STDOUT_BYTES = 8192


class SSHExecutor:
    """Paramiko SSH 客户端封装。"""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        key_path: str | None = None,
        timeout: int = 30,
    ) -> None:
        """初始化 SSHExecutor。

        Args:
            host: 目标主机 IP 或域名。
            port: SSH 端口。
            username: 登录用户名。
            password: 密码（与 ``key_path`` 二选一或作为密钥 passphrase）。
            key_path: 私钥路径（``~`` 会被展开）。
            timeout: 默认命令超时秒数。
        """
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self._key_path = key_path
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        """建立 SSH 连接；已连接时幂等返回。

        Raises:
            SSHConnectionError: 认证失败或网络不可达。
        """
        if self.is_connected():
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
            "banner_timeout": 30,
            "auth_timeout": 30,
        }
        if self._key_path:
            pkey_path = Path(self._key_path).expanduser().resolve()
            if not pkey_path.exists():
                raise SSHConnectionError(f"Private key not found: {pkey_path}")
            kwargs["key_filename"] = str(pkey_path)
            if self._password:
                kwargs["passphrase"] = self._password
        elif self._password:
            kwargs["password"] = self._password
        else:
            raise SSHConnectionError("Neither password nor key_path provided")

        try:
            client.connect(**kwargs)
        except (paramiko.SSHException, OSError) as e:
            raise SSHConnectionError(
                f"Failed to connect {self.username}@{self.host}:{self.port}: {e}"
            ) from e

        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)

        self._client = client
        logger.info("SSH connected: {}@{}:{}", self.username, self.host, self.port)

    def is_connected(self) -> bool:
        """判断当前连接是否可用。"""
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return bool(transport and transport.is_active())

    def run(
        self,
        command: str,
        timeout: int | None = None,
        sudo: bool = False,
    ) -> ExecResult:
        """执行一条 shell 命令。

        断连会自动触发一次重连。所有底层 paramiko 异常统一转为
        ``SSHExecutionError``；命令本身非 0 退出码不视为异常，
        调用方通过 ``result.return_code`` / ``result.success`` 判断。

        Args:
            command: 原始命令字符串。
            timeout: 单次执行超时（秒），None 则用默认。
            sudo: 是否前缀 ``sudo -n``（``-n`` 避免交互式密码卡死）。

        Returns:
            ExecResult 封装 stdout / stderr / return_code / duration_ms。

        Raises:
            SSHExecutionError: 执行过程异常。
        """
        if not self.is_connected():
            self.connect()
        assert self._client is not None

        actual_cmd = f"sudo -n {command}" if sudo else command
        effective_timeout = timeout if timeout is not None else self.timeout
        logger.debug("SSH run: {}", actual_cmd)

        start = time.monotonic()
        try:
            _, stdout, stderr = self._client.exec_command(actual_cmd, timeout=effective_timeout)
            out_bytes = stdout.read()
            err_bytes = stderr.read()
            rc = stdout.channel.recv_exit_status()
        except paramiko.SSHException as e:
            raise SSHExecutionError(f"SSH exec failed: {e}") from e
        except Exception as e:
            raise SSHExecutionError(f"Unexpected SSH error: {e}") from e
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)

        out_text = out_bytes.decode("utf-8", errors="replace")
        err_text = err_bytes.decode("utf-8", errors="replace")

        truncated = False
        if len(out_text) > _MAX_STDOUT_BYTES:
            out_text, truncated = safe_truncate(out_text, max_chars=_MAX_STDOUT_BYTES)
        if len(err_text) > _MAX_STDOUT_BYTES:
            err_text, err_trunc = safe_truncate(err_text, max_chars=_MAX_STDOUT_BYTES)
            truncated = truncated or err_trunc

        result = ExecResult(
            command=actual_cmd,
            stdout=out_text,
            stderr=err_text,
            return_code=rc,
            duration_ms=elapsed_ms,
            truncated=truncated,
        )
        logger.info("SSH done: rc={} duration_ms={} truncated={}", rc, elapsed_ms, truncated)
        return result

    def close(self) -> None:
        """幂等关闭连接。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            finally:
                self._client = None
                logger.info("SSH closed: {}@{}", self.username, self.host)

    def __enter__(self) -> SSHExecutor:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
