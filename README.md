# LinuxAgent · 操作系统智能代理

> **AI Hackathon 2026 – OS 智能代理赛道** 参赛作品  
> 用自然语言操控远端 Linux 服务器，内置四层安全防护，全程推理可观察

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![Gradio](https://img.shields.io/badge/UI-Gradio-orange.svg)](https://gradio.app)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-green.svg)](https://deepseek.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 项目简介

用户输入一句自然语言（如"找出占用磁盘最大的文件"），LinuxAgent 自动完成：

```
自然语言输入
    → 意图过滤（BLOCKED 类意图直接拦截，< 50ms）
    → LLM 规划（Plan-Act，1-5 步可视化计划）
    → ReAct 循环（思考 → 工具调用 → 观察 → 汇总）
    → 四层安全评估（BLOCKED 拦截 / HIGH 二次确认）
    → SSH 执行真实命令
    → 结构化中文回复
```

全程推理链实时可见，每一步决策均有 JSONL 记录，可审计、可复现。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 自然语言运维 | DeepSeek Function Calling 驱动 19 个内置工具 |
| Plan-Act 规划 | LLM 把任务拆解为 1-5 步，Web UI 实时看板 ⬜→🔄→✅ |
| 四层安全防护 | 意图过滤 + 正则规则(21条) + 静态启发 + LLM 语义 |
| 风险分级 | BLOCKED（直接拦截）/ HIGH（二次确认）/ MEDIUM / LOW |
| 全程可观察 | TraceStore 记录全量 AgentEvent，JSONL 落盘可审计 |
| 双端 UI | Gradio Web（流式+确认弹窗）+ Rich CLI |
| 多轮对话 | SessionContext 跨 Turn 上下文记忆，LRU 20 会话隔离 |
| 跨发行版 | openEuler / Ubuntu / CentOS 自适应（env probe） |
| 可扩展 | `@tool` 装饰器添工具零样板；`rules.yaml` 添规则零代码 |

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 推荐 3.11/3.12 |
| Conda | 任意 | 可选，用于虚拟环境管理 |
| DeepSeek API Key | — | 用于 LLM 推理（[申请地址](https://platform.deepseek.com)） |
| 目标 Linux 服务器 | SSH 可达 | 需要 root 或 sudo 权限 |

---

## 快速开始

### 第一步：获取代码

```bash
git clone <repo-url>
cd LinuxAgent
```

### 第二步：创建虚拟环境

**方式 A：Conda（推荐）**

```bash
conda create -n linuxagent python=3.11 -y
conda activate linuxagent
```

**方式 B：venv**

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate
```

### 第三步：安装依赖

```bash
pip install -r requirements.txt
```

### 第四步：配置密钥与连接

复制配置模板：

```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

**编辑 `.env`**（存放敏感信息，不提交 Git）：

```dotenv
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SSH_PASSWORD=your_server_password
```

**编辑 `config.yaml`**（修改 SSH 连接信息）：

```yaml
ssh:
  host: 你的服务器IP        # 必填，例如 192.168.1.100
  port: 22
  username: root            # SSH 登录用户名
  auth_mode: password       # 密码认证，或改为 key 使用密钥

agent:
  max_steps: 8              # ReAct 最大步数（建议 8-15）

safety:
  enable_semantic_review: true   # 是否启用 LLM 语义风险评估（会消耗 API）
```

> **注意**：`config.yaml` 中不要填写密码，密码统一写在 `.env` 的 `SSH_PASSWORD`。

### 第五步：启动

**Web UI（推荐，支持完整功能）：**

```bash
python app_web.py
```

浏览器打开 [http://localhost:7860](http://localhost:7860)

**CLI（轻量终端模式）：**

```bash
python app_cli.py
```

---

## 界面说明

启动 Web UI 后，界面分为以下区域：

```
┌─────────────────────────────┬──────────────────────┐
│                             │  执行计划看板          │
│      对话区（主区域）         │  ⬜→🔄→✅ 步骤进度   │
│                             │                      │
│  工具调用折叠块 + 最终回复    │  风险确认卡片          │
│                             │  （HIGH 级弹出）       │
├─────────────────────────────┴──────────────────────┤
│  实时推理链  |  历史轮次回溯  |  系统日志  |  操作记录  │
└────────────────────────────────────────────────────┘
```

| UI 元素 | 功能 |
|---------|------|
| 对话区 | 输入自然语言，查看工具执行结果和最终回复 |
| 执行计划看板 | 实时显示当前任务的 1-5 步计划与完成进度 |
| 风险确认卡片 | HIGH 风险操作弹出，需点击"同意执行"或"拒绝" |
| 实时推理链 Tab | 暗色时间线，显示 LLM 每步推理过程 |
| 历史轮次回溯 Tab | 查看历史对话的完整 AgentEvent 链 |
| 系统日志 Tab | loguru 日志，含 SSH 执行记录 |
| 操作记录 Tab | TraceStore JSONL 文件内容 |

---

## 演示场景

### 基础查询（LOW 风险，直接执行）

```
查看根目录磁盘使用情况
列出 CPU 占用最高的 10 个进程
80 端口是谁在监听？
查看系统负载和运行时长
当前服务器的操作系统版本是什么？
找出 /home 目录下最大的 5 个文件
```

### 安全拦截演示（BLOCKED，无需确认）

```
格式化 /dev/sda          → 意图过滤层直接拦截（< 50ms）
帮我删除 /etc 目录        → 意图过滤层直接拦截
执行 :(){ :|:& };:       → 规则层拦截（fork bomb）
用 dd 把 /dev/sda 清零   → 规则层拦截
```

### 高风险确认演示（HIGH，弹出确认框）

```
关闭防火墙               → 弹出确认卡片，可选择同意/拒绝
停止 SSH 服务            → 弹出确认卡片（警告远程连接失效）
删除用户 testuser 并清除家目录  → 弹出确认卡片
```

### 多步任务演示（Plan-Act 看板）

```
帮我找出磁盘占用最大的目录，并告诉我能否安全清理
检查 80 和 443 端口状态，告诉我服务是否正常
创建用户 devops，主目录 /home/devops，确认是否成功
```

---

## 项目结构

```
LinuxAgent/
├── app_web.py                   Gradio Web 入口（主要使用）
├── app_cli.py                   Rich CLI 入口
├── config.yaml.example          配置模板（复制为 config.yaml）
├── .env.example                 密钥模板（复制为 .env）
├── requirements.txt             Python 依赖
│
├── linuxagent/                  核心库
│   ├── config.py                三层配置（.env > config.yaml > 默认值）
│   ├── core/
│   │   ├── agent.py             ReAct 主循环，产出 10 种 AgentEvent
│   │   ├── planner.py           Plan-Act 规划层（LLM JSON Mode）
│   │   ├── llm.py               DeepSeek LLM 网关（tenacity 重试）
│   │   ├── context.py           SessionContext / EnvSnapshot
│   │   ├── prompts.py           系统 Prompt / Planner Prompt / 风险审核 Prompt
│   │   └── trace_store.py       全量推理追踪（内存 + JSONL）
│   ├── executor/
│   │   └── ssh_executor.py      SSH 执行器（paramiko，keepalive，自动重连）
│   ├── tools/
│   │   ├── base.py              @tool 装饰器 + ToolRegistry + ToolResult
│   │   ├── system.py            系统信息工具
│   │   ├── disk.py              磁盘工具
│   │   ├── file.py              文件工具
│   │   ├── process.py           进程/端口/服务工具
│   │   ├── user.py              用户管理工具
│   │   ├── network.py           网络工具
│   │   └── shell.py             run_command 通用执行工具
│   ├── safety/
│   │   ├── intent_filter.py     第 0 层：自然语言意图过滤（3 条规则）
│   │   ├── rules.yaml           第 1 层：21 条正则安全规则
│   │   ├── risk_engine.py       三闸风险引擎（规则→静态→语义）
│   │   └── confirm.py           确认卡片格式化
│   └── ui/
│       ├── web_components.py    Gradio 事件流渲染
│       ├── trace_view.py        TraceStore 时间线渲染
│       ├── cli_components.py    Rich 终端组件
│       └── session_pool.py      LRU 会话池（上限 20）
│
├── scripts/
│   └── md_to_pdf.py             文档 PDF 导出工具
├── tests/                       单元测试 + 集成测试
├── docs/
│   ├── AGENT_CONFIG.md          Agent 配置说明（含 Prompt 原文）
│   ├── DESIGN.md                系统设计文档
│   ├── TEST_SCENARIOS.md        自测验证材料（演示场景 + 可观察输出）
│   ├── pdf/                     以上文档的 PDF 版本
│   ├── SECURITY.md              安全设计文档
│   └── LINUX_SETUP.md           目标服务器配置指南
└── logs/                        运行时生成
    ├── app.log                  系统日志
    └── traces/<session>.jsonl   操作记录（TraceStore）
```

---

## 配置说明

### 完整 `config.yaml` 参数

```yaml
llm:
  provider: deepseek
  base_url: https://api.deepseek.com/v1
  model: deepseek-chat
  temperature: 0.2          # 较低温度保证结构化输出稳定
  max_tokens: 2048
  timeout_s: 60

ssh:
  host: 192.168.1.100       # 必填：目标服务器 IP
  port: 22
  username: root
  auth_mode: password        # password 或 key
  key_path: ~/.ssh/id_rsa   # auth_mode=key 时生效
  default_timeout_s: 30

agent:
  max_steps: 8               # ReAct 最大迭代步数
  history_limit: 20          # 保留最近 N 条对话历史
  language: zh-CN

safety:
  rules_path: linuxagent/safety/rules.yaml
  enable_semantic_review: true    # 关闭可节省 API 消耗
  semantic_timeout_s: 5.0
  require_confirmation_on_high: true

logging:
  level: INFO
  file: logs/app.log
  rotation: "1 day"
  retention: "7 days"

ui:
  web_host: 0.0.0.0
  web_port: 7860
```

### 完整 `.env` 参数

```dotenv
# DeepSeek API Key（必填）
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# SSH 密码（auth_mode=password 时必填）
SSH_PASSWORD=your_server_password
```

---

## 安全规则说明

系统内置 **21 条安全规则**（`linuxagent/safety/rules.yaml`），分三个等级：

| 等级 | 条数 | 行为 | 示例 |
|------|------|------|------|
| BLOCKED | 11 | 直接拦截，不弹确认 | `mkfs`、`rm -rf /etc`、`wipefs`、`dd of=/dev/sd*`、`fdisk /dev/sd*` |
| HIGH | 8 | 弹出二次确认卡片 | `reboot`、`iptables -F`、`systemctl stop sshd` |
| MEDIUM | 2 | 放行但日志记录 | `useradd`、`systemctl restart` |

此外，**意图过滤层**（`intent_filter.py`）在 LLM 规划前就能识别并拦截中文自然语言的危险意图（"格式化磁盘"、"删除/etc"等），响应时间 < 50ms。

如需添加自定义规则，直接在 `rules.yaml` 追加一条记录，重启生效：

```yaml
- id: rule.my_custom
  description: 禁止删除日志目录
  pattern: '\brm\b.*-rf.*\b/var/log\b'
  level: BLOCKED
  reason: 删除日志目录会导致系统日志丢失
  suggestions:
    - 改为清理日志文件而非删除目录
```

---

## 运行测试

```bash
# 单元测试（无需 SSH / API）
pytest -q

# 风险引擎专项测试
pytest tests/test_risk_engine.py -v

# 集成测试（需要真实 SSH + DeepSeek API）
# Windows PowerShell
$env:LINUXAGENT_INTEGRATION="1"
pytest tests/test_integration.py -v

# Linux / macOS
LINUXAGENT_INTEGRATION=1 pytest tests/test_integration.py -v
```

---

## 常见问题

**Q：启动时报 `SSH connection failed`**  
A：检查 `config.yaml` 中的 `ssh.host` / `ssh.username`，以及 `.env` 中的 `SSH_PASSWORD` 是否正确。可先用 `ssh root@<host>` 手动验证连通性。

**Q：LLM 调用报 `AuthenticationError`**  
A：检查 `.env` 中的 `DEEPSEEK_API_KEY` 是否有效，或访问 [DeepSeek 控制台](https://platform.deepseek.com) 确认 API Key 状态。

**Q：风险确认卡片弹出后点击无响应**  
A：刷新页面后重试；若频繁发生，检查浏览器控制台是否有 WebSocket 断连错误。

**Q：想关闭 LLM 语义风险审核（节省 API 费用）**  
A：在 `config.yaml` 中设置 `safety.enable_semantic_review: false`，重启生效。

**Q：如何查看完整的推理过程？**  
A：Web UI 底部 "实时推理链" Tab 实时显示；历史数据保存在 `logs/traces/<session_id>.jsonl`，每行一个 AgentEvent JSON。

**Q：如何在 openEuler / CentOS 等非 Ubuntu 系统上使用？**  
A：系统在每次连接时自动探测目标服务器的发行版和包管理器，并注入 Agent Prompt，无需手动配置。

---

## 技术栈

| 组件 | 库 / 服务 |
|------|----------|
| LLM | DeepSeek-Chat（OpenAI 兼容接口） |
| LLM 客户端 | `openai` >= 1.30 |
| SSH 执行 | `paramiko` >= 3.4 |
| Web UI | `gradio` >= 4.20 |
| CLI UI | `rich` >= 13 |
| 配置管理 | `pydantic-settings` >= 2.2 |
| 日志 | `loguru` >= 0.7 |
| LLM 重试 | `tenacity` >= 8.2 |
| 规则解析 | `pyyaml` >= 6.0 |
| 测试 | `pytest` + `pytest-asyncio` |

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/AGENT_CONFIG.md](docs/AGENT_CONFIG.md) | Agent 配置说明、核心 Prompt 原文、工具定义 |
| [docs/DESIGN.md](docs/DESIGN.md) | 系统架构设计、意图解析算法、设计取舍 |
| [docs/TEST_SCENARIOS.md](docs/TEST_SCENARIOS.md) | 自测验证材料、演示场景、可观察输出说明 |
| [docs/LINUX_SETUP.md](docs/LINUX_SETUP.md) | 目标 Linux 服务器配置指南 |
| [docs/SECURITY.md](docs/SECURITY.md) | 安全设计详述 |

---

## License

[MIT](LICENSE)
