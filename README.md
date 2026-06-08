# 🦜 Parrot Ops

**我教会了我的鹦鹉写 Shell**

把终端操作录制为 AI Agent 可执行的运维 Skill。人教一次，Agent 用万次。

> *I taught my parrot to write Shell — record once, let AI execute a thousand times.*

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

## 模块

| 模块 | 在哪运行 | 干什么 |
|---|---|---|
| `parrot-recorder` | 目标机 | asciinema 录制 → pyte 清洗 → LLM 提炼 → Skill YAML |
| `parrot-agent` | 目标机 | 接收 Skill，本地执行，审计日志，回滚，并发控制 |
| `parrot-mcp` | 本机 | Skill YAML → MCP Tool，对接 Claude Code |

## 文档

- [录制模块](docs/parrot-recorder-spec.md)
- [执行引擎](docs/parrot-agent-spec.md)
- [MCP 接口](docs/parrot-mcp-spec.md)
- [录制场景验证](docs/recording-scenarios.md)
