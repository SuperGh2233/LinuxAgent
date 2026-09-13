"""SSH 执行结果数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, computed_field

from linuxagent.utils.helpers import safe_truncate


class ExecResult(BaseModel):
    """一次 SSH 执行的结果封装。"""

    command: str
    stdout: str
    stderr: str
    return_code: int
    duration_ms: int
    truncated: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """退出码为 0 即视为成功。"""
        return self.return_code == 0

    def to_brief(self, max_chars: int = 2048) -> str:
        """把 stdout / stderr 拼成给 LLM 看的紧凑字符串。

        超过 ``max_chars`` 会再次截断并在 header 标注。
        """
        body_parts: list[str] = []
        if self.stdout:
            body_parts.append(f"[stdout]\n{self.stdout}")
        if self.stderr:
            body_parts.append(f"[stderr]\n{self.stderr}")
        body = "\n\n".join(body_parts) or "(no output)"
        truncated_body, was_trunc = safe_truncate(body, max_chars=max_chars)
        header = f"$ {self.command}\nexit_code={self.return_code} duration_ms={self.duration_ms}"
        if was_trunc or self.truncated:
            header += " truncated=True"
        return f"{header}\n{truncated_body}"
