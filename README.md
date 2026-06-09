# 🦜 Parrot Ops

**演示一次，处处执行**

为 AI Agent 提供安全、稳定的远程运维技能。人教一次，Agent 用万次。

> *Demonstrate once, execute anywhere — secure, stable remote skills for AI Agents.*

## Quick Start

```bash
# 1. 目标机：安装并启动 agent
git clone https://github.com/SumrainChan/parrot-ops.git
cd parrot-ops
pip install ./parrot-recorder ./parrot-agent
parrot-agent --port 9090 &

# 2. 目标机：录制操作
parrot learn -t "部署 user-api 服务"
# ... 敲你的运维命令 ... exit

# 3. 本机：安装 mcp，配置 Claude Code
cd parrot-ops
pip install ./parrot-mcp
# 编辑 ~/.claude/mcp.json:
# { "mcpServers": { "parrot": {
#     "command": "parrot-mcp",
#     "args": ["--agent", "http://<目标机IP>:9090"]
# }}}
```

对 Claude Code 说：**"list skills"** — 就能看到刚录制的 Skill。

## 它能做什么

| 场景 | 之前 | 之后 |
|---|---|---|
| 部署服务 | 每次手动敲命令 | 一句 "execute deploy-service" |
| 团队新人接手 | 翻 Wiki 或问老同事 | Agent 调用已有的 Skill |
| 重复性运维 | 每次都靠记忆 | Skill YAML 固化，可审计可回滚 |
| 生产环境操作 | 提心吊胆怕敲错 | 人类审核一次，Agent 执行 N 次 |

## 架构

<details>
<summary>展开架构图</summary>

```
目标机 (Linux)                        本机 (Win/Mac/Linux)

 Record once                            Execute everywhere
     │                                       │
     ▼                                       ▼
parrot-recorder     Skill YAML          parrot-mcp
   (录制→生成)  ───────────────>  (MCP 标准接口)
     │                                       │
     ├─ parrot-agent (本地执行引擎)          │
     │    :9090 HTTP API                     │
     └───────────────────────────────────────┘
                                              │
                                         Claude Code
                                       "execute deploy-service"
```

</details>

## 安装

| 模块 | 方式 | 依赖 |
|---|---|---|
| parrot-recorder | `pip install ./parrot-recorder` | Python ≥ 3.10, asciinema (自动安装) |
| parrot-agent | `pip install ./parrot-agent` | Python ≥ 3.10, Linux |
| parrot-mcp | `pip install ./parrot-mcp` | Python ≥ 3.10 |

> 尚未发布到 PyPI，当前从源码安装。后续可直接 `pip install parrot-recorder`。

## 使用示例

**录制一个 Docker 部署 Skill：**

```bash
parrot learn -t "部署 user-api 服务"
[parrot] 开始录制... (输入 exit 结束)

root@host:~$ docker build -t user-api:latest .
root@host:~$ docker stop user-api && docker rm user-api
root@host:~$ docker run -d -p 8080:8080 --name user-api user-api:latest
root@host:~$ exit

[parrot] 检测到 3 条命令，正在生成 Skill YAML...
[parrot] 已保存: skills/deploy-user-api.skill.yaml
Register to agent? Enter URL or blank to skip: http://127.0.0.1:9090
[parrot] Registered 'deploy-user-api' to http://127.0.0.1:9090
```

**Agent 调用：**

```
Claude Code: "list skills"
 → deploy-user-api, reload-nginx, health-check

Claude Code: "execute deploy-user-api, service_name=user-api"
 → parrot-agent 本地执行: build → stop-old → start
 → 完成: build ✅, stop-old ✅, start ✅
```

## 命令参考

| 命令 | 在哪运行 | 说明 |
|---|---|---|
| `parrot learn` | 目标机 | 录制操作 → 生成 Skill YAML |
| `parrot learn --skip-llm` | 目标机 | 录制 → 保存中间数据（离线） |
| `parrot compose <file> -t "描述"` | 任意 | 从中间数据生成 Skill YAML |
| `parrot register <file> --agent <url>` | 任意 | 注册 Skill 到 agent |
| `parrot new` | 任意 | 交互式创建 Skill（不录制） |
| `parrot validate <file>` | 任意 | 校验 Skill YAML |
| `parrot-agent --port 9090` | 目标机 | 启动执行引擎 |
| `parrot-mcp --agent <url>` | 本机 | 启动 MCP 服务 |

## 配置

**parrot-recorder：** 项目根目录 `.env` 文件

```bash
PARROT_LLM_BACKEND=anthropic       # 或 openai
PARROT_MODEL=claude-sonnet-4-6     # 模型选择
ANTHROPIC_API_KEY=sk-ant-xxx       # API key
# 代理或本地模型：
# ANTHROPIC_BASE_URL=https://your-proxy.com
# OPENAI_BASE_URL=http://localhost:11434/v1
```

**parrot-agent：** 命令行参数

```bash
parrot-agent --port 9090 --bind 127.0.0.1
# --port  监听端口 (默认 9090)
# --bind  绑定地址 (默认 127.0.0.1，外部访问用 0.0.0.0)
```

**parrot-mcp：** 命令行参数，或 Claude Code `mcp.json`

```bash
parrot-mcp --agent http://<目标机>:9090
# --agent  parrot-agent 地址
```

## 文档

- [录制模块](docs/parrot-recorder-spec.md)
- [执行引擎](docs/parrot-agent-spec.md)
- [MCP 接口](docs/parrot-mcp-spec.md)
- [录制场景验证](docs/recording-scenarios.md)
