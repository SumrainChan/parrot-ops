# Parrot Ops

**Demonstrate once, execute anywhere.** 录制一次终端操作，生成 AI Agent 可复用的运维 Skill。

## Quick Start

```bash
# 1. 目标机：安装并启动 agent
pip install parrot-recorder parrot-agent
parrot-agent --port 9090 &

# 2. 目标机：录制操作
parrot learn -t "部署 user-api 服务"
# ... 执行你的运维操作 ... exit

# 3. 本机：配置 Claude Code MCP
# ~/.claude/mcp.json
{
  "mcpServers": {
    "parrot": {
      "command": "parrot-mcp",
      "args": ["--agent", "http://<目标机IP>:9090"]
    }
  }
}
```

然后对 Claude Code 说：**"list skills"** — 就能看到刚录制的 Skill。

## 它能做什么

| 场景 | 之前 | 之后 |
|---|---|---|
| 部署服务 | 每次手动敲命令 | 一句 "execute deploy-user-api" |
| 团队新人接手 | 翻 Wiki 或问老同事 | Agent 调用已有的 Skill |
| 生产环境操作 | 提心吊胆怕敲错 | Skill 经过审核，可审计可回滚 |

## 模块

| | 安装 | 在哪运行 | 干什么 |
|---|---|---|---|
| parrot-recorder | `pip install parrot-recorder` | 目标机 | 录制操作 → 生成 Skill |
| parrot-agent | `pip install parrot-agent` | 目标机 | 执行 Skill |
| parrot-mcp | `pip install parrot-mcp` | 本机 | 对接 Claude Code |

## 文档

- [录制模块规格](docs/parrot-recorder-spec.md)
- [执行引擎规格](docs/parrot-agent-spec.md)
- [MCP 接口规格](docs/parrot-mcp-spec.md)
- [录制场景验证](docs/recording-scenarios.md)
- [三种执行方式对比](docs/execution-approaches-comparison.md)
