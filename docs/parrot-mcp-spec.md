# parrot-mcp 需求分析与功能规格

> Phase 2 — MCP 标准化接口：让 AI Agent 原生发现和调用运维 Skill

## 一、核心定位

部署在本地（Agent 侧）的 MCP Server。将 Skill YAML 库转换为 MCP Tool 定义，让 Claude Code 等 Agent 通过 function call 原生调用。内部路由到远程 parrot-agent，异常时将 agent 的结构化错误上下文返回给 Agent 自主决策。

**核心原则**：parrot-mcp 只管发现和路由。正常的交给 parrot-agent，异常的交给 AI Agent。

## 二、架构位置

```
本地 (Windows/macOS/Linux)
├── Claude Code ──MCP stdio──> parrot-mcp ──HTTP──> parrot-agent (目标机)
│                                                      │
│   正常: execute_skill("deploy", {...})                │ 按 Skill 执行
│         → agent 返回 success                          │ → 完成
│                                                      │
│   异常: agent 返回 blocked                            │
│         → mcp 返回结构化错误给 Agent                   │ retry/rollback 已尝试
│         → Agent 自己 SSH 排查                         │ → 暂停等恢复
│         → Agent 调用 resume_skill / skip_step          │
```

## 三、MCP Tool 定义

### 1. list_skills

```json
{
  "name": "list_skills",
  "description": "列出所有可用的运维 Skill",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "按名称或描述搜索"},
      "category": {"type": "string", "enum": ["deploy", "check", "config", "all"]}
    }
  }
}
```

### 2. execute_skill

```json
{
  "name": "execute_skill",
  "description": "执行一个运维 Skill。正常路径由远程 agent 自动完成。遇到异常时返回上下文供排查。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "skill_name": {"type": "string", "description": "Skill 名称"},
      "params": {"type": "object", "description": "Skill 参数"},
      "target": {"type": "string", "description": "目标主机，未指定用默认"}
    },
    "required": ["skill_name"]
  }
}
```

**正常返回：**

```json
{
  "status": "completed",
  "task_id": "abc123",
  "steps": [
    {"id": "build", "status": "ok", "duration_ms": 12340},
    {"id": "deploy", "status": "ok", "duration_ms": 2340}
  ]
}
```

**异常返回（agent 无法自动处理）：**

```json
{
  "status": "blocked",
  "task_id": "abc123",
  "failed_step": {
    "id": "deploy",
    "command": "docker run -d -p 8080:8080 --name user-api ...",
    "output": "port 8080 already in use by container abc123"
  },
  "attempted": ["retry x2"],
  "completed_steps": [{"id": "build", "status": "ok"}]
}
```

Agent 拿到 blocked 状态后，用自己 SSH 排查，处理完毕后调用 resume。

### 3. get_task_status

查询异步任务状态。

### 4. resume_skill / skip_step / abort_skill

```json
{
  "name": "resume_skill",
  "description": "恢复一个被阻塞的 Skill（重试失败步骤）",
  "inputSchema": {
    "type": "object",
    "properties": {"task_id": {"type": "string"}},
    "required": ["task_id"]
  }
}
```

skip_step 和 abort_skill 同理。

---

## 四、执行流程

```
Agent: execute_skill("deploy-service", {service: "user-api"})
  │
  ▼
parrot-mcp:
  1. 读取 Skill YAML，校验 params
  2. 按 target 路由到 parrot-agent (HTTP)
  3. 轮询 GET /v1/tasks/{task_id}
  │
  ├── agent 返回 completed
  │     → mcp 返回成功给 Agent
  │
  └── agent 返回 blocked
        → mcp 返回结构化错误给 Agent
        → Agent 自主 SSH 排查
        → Agent 调用 resume_skill / skip_step
        → mcp 转发到 agent
```

---

## 五、Skill → MCP Tool 转换

| Skill YAML 字段 | MCP Tool 字段 |
|---|---|
| `name` | tool name |
| `description` | tool description（增强：拼接步骤摘要 + 回滚提示） |
| `parameters[*].name, type, default, description` | inputSchema properties |
| `parameters[*].required` | inputSchema required |
| `steps`, `rollback`, `preconditions` | 不暴露（agent 内部处理） |

---

## 六、部署

```bash
pip install parrot-mcp
```

```json
// .claude/mcp.json
{
  "mcpServers": {
    "parrot": {
      "command": "parrot-mcp",
      "args": []
    }
  }
}
```

```yaml
# ~/.config/parrot/mcp-config.yaml
targets:
  - name: dev
    host: 192.168.1.10
    agent_port: 9090
  - name: prod
    host: prod.example.com
    agent_port: 9090

default_target: dev
skill_dir: ./skills
```

---

## 七、与 parrot-agent 的边界

| 职责 | parrot-mcp | parrot-agent |
|---|---|---|
| MCP 协议 | ✅ stdio | — |
| Skill → Tool 转换 | ✅ JSON Schema | — |
| 路由到 agent | ✅ HTTP | — |
| 结构化错误上报 | ✅ 转发给 Agent | ✅ 生成错误上下文 |
| 确定性执行 | — | ✅ |
| 审计日志 | — | ✅ |
| 并发/回滚 | — | ✅ |
| SSH 排查 | 不参与 | —（AI Agent 做） |

---

## 八、明确不做

- SSH 降级执行 — 异常时 Agent 自己 SSH，mcp 不参与
- 自定义 transport — 仅 stdio
- 身份认证 — Phase 3
