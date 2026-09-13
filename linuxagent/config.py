"""配置体系：yaml + .env + 默认值三层合并。

设计约定：
- ``config.yaml`` 管理非敏感配置（主机、模型名、超时等）
- ``.env`` 管理敏感字段（API Key、密码）
- pydantic 默认值兜底

合并优先级：init 参数（yaml 值）> 环境变量 > pydantic 默认值。
由于敏感字段不出现在 yaml，它们天然由 env 读取，不会被 yaml 覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from linuxagent.utils.errors import ConfigurationError


class LLMConfig(BaseModel):
    """LLM 服务配置。"""

    provider: Literal["deepseek", "openai", "openai_compatible"] = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(2048, ge=128, le=8192)
    timeout_s: int = Field(60, ge=5, le=300)


class SSHConfig(BaseModel):
    """SSH 连接配置。"""

    host: str = "192.168.1.100"
    port: int = Field(22, ge=1, le=65535)
    username: str = "root"
    auth_mode: Literal["password", "key"] = "password"
    key_path: str = "~/.ssh/id_rsa"
    default_timeout_s: int = Field(30, ge=5, le=600)


class AgentConfig(BaseModel):
    """Agent 核心行为配置。"""

    max_steps: int = Field(8, ge=1, le=20)
    history_limit: int = Field(20, ge=2, le=100)
    language: str = "zh-CN"


class SafetyConfig(BaseModel):
    """安全与风险控制配置。"""

    rules_path: str = "linuxagent/safety/rules.yaml"
    enable_semantic_review: bool = True
    semantic_timeout_s: float = Field(5.0, ge=0.5, le=30.0)
    require_confirmation_on_high: bool = True


class LoggingConfig(BaseModel):
    """日志配置。"""

    level: str = "INFO"
    file: str = "logs/app.log"
    rotation: str = "1 day"
    retention: str = "7 days"


class UIConfig(BaseModel):
    """UI 配置。"""

    web_host: str = "0.0.0.0"
    web_port: int = Field(7860, ge=1, le=65535)
    theme: str = "default"


class Settings(BaseSettings):
    """顶层配置容器。

    敏感字段 ``deepseek_api_key`` 与 ``ssh_password`` 从环境变量读取。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    ui: UIConfig = Field(default_factory=UIConfig)

    deepseek_api_key: SecretStr = Field(default=SecretStr(""))
    ssh_password: SecretStr = Field(default=SecretStr(""))


def load_yaml_config(path: Path) -> dict[str, Any]:
    """从 yaml 文件加载配置。

    Args:
        path: yaml 文件路径。

    Returns:
        解析后的 dict；文件不存在时返回空 dict。

    Raises:
        ConfigurationError: yaml 解析失败或顶层不是 dict。
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Failed to parse YAML {path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigurationError(f"Invalid YAML top-level in {path}, expected dict")
    return data


@lru_cache(maxsize=1)
def get_settings(config_path: str = "config.yaml") -> Settings:
    """获取 Settings 单例。

    加载流程：
    1. 读取 ``config.yaml``（可选）→ dict
    2. 构造 ``Settings(**yaml_dict)``（缺失项走默认值）
    3. pydantic-settings 自动用环境变量填充敏感字段

    Args:
        config_path: 配置文件路径，默认 ``config.yaml``。

    Returns:
        Settings 单例。

    Raises:
        ConfigurationError: 配置加载或校验失败。
    """
    cfg_path = Path(config_path)
    yaml_data = load_yaml_config(cfg_path)
    try:
        return Settings(**yaml_data)
    except Exception as e:
        raise ConfigurationError(f"Failed to build Settings from {cfg_path}: {e}") from e
