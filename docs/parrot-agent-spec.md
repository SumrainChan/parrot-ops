# parrot-agent 需求分析与功能规格

> Phase 2 — 远程执行引擎：稳定、可靠、安全的确定性运维执行

## 一、核心定位

部署在目标 Linux 主机上的轻量守护进程。按 Skill YAML 的确定性路径执行操作，提供审计日志、并发控制和可靠回滚。当遇到 Skill 定义之外的异常时，结构化上报上下文给本地 AI Agent 动态决策。

**核心原则**：parrot-agent 做确定性，本地 Agent 做灵活性。两者分工，不替代。

## 二、执行范式对比

### 方式 A：Agent SSH 自由探索
Agent 自主规划步骤，MCP SSH 逐条执行。灵活但不可预测、无知识积累、Token 消耗高。适用排障和新问题。

### 方式 B：Agent + Skill YAML + SSH 逐步骤（Phase 1）
Agent 读 Skill YAML，SSH 逐步执行。操作确定但网络依赖、无审计、无并发控制。

### 方式 C：parrot-agent 常驻执行（Phase 2）
守护进程本地执行 Skill，审计/回滚/并发内置。异常时暂停并上报 AI Agent 推理处理。

| | A: SSH 探索 | B: Skill + SSH | C: Skill + Agent |
|---|---|---|---|
| 确定性 | 低 | 高 | 高 |
| 灵活性 | 极高 | 低 | **高（异常时 Agent 介入）** |
| 审计日志 | 无 | 无 | ✅ 结构化 JSON |
| 网络中断恢复 | 无 | 无 | ✅ task_id |
| 回滚可靠性 | — | SSH 逐条 | ✅ 本地执行 |
| 并发控制 | 无 | 无 | ✅ |

---

## 三、与本地 AI Agent 的分工

parrot-agent 不是替代 Agent，而是为 Agent 提供"稳定的已知路径"。异常时 Agent 接管。

```
parrot-agent (远程守护进程)
  ├── 正常步骤 → 按 Skill 执行 → 完成
  ├── 可预期异常 → retry / rollback / skip (内建)
  └── 不可预期异常 → 暂停 + 结构化上报

本地 AI Agent
  ├── 正常: execute_skill("deploy", {...}) → 等待 → 收到完成
  ├── 异常: 收到 blocked 状态 + 错误上下文
  │         → 用自己 SSH 动态排查
  │         → 处理完毕后 resume 或 skip
  └── 兜底: 询问人类
```

### 分工表

| | parrot-agent | 本地 AI Agent |
|---|---|---|
| 正常步骤执行 | ✅ 按 Skill 走 | 无感，一次调用等结果 |
| retry / rollback / skip | ✅ 内建 | 无感 |
| 预期外输出 / 环境异常 | ✅ 暂停 + 结构化上报 | ✅ SSH 排查、推理决策 |
| 恢复执行 | ✅ resume / skip API | 决策"如何恢复" |
| 审计日志 | ✅ JSON, 7天 TTL | 查阅 |
| 并发锁 | ✅ flock | 无感 |

---

## 四、核心能力

### P0 — MVP

| 能力 | 说明 |
|---|---|
| **本地执行** | 按 Skill YAML 步骤在本地 shell 执行 |
| **回滚** | 步骤失败时本地执行 rollback（不依赖网络） |
| **结构化错误** | 不可处理异常时返回完整上下文（失败步骤、输出、已尝试操作、已完成步骤） |
| **恢复执行** | `resume`（重试当前步骤）/ `skip`（跳过继续）/ `abort`（终止+回滚） |
| **审计日志** | 每次执行写入 `~/.parrot/audit/<task_id>.json`，7天清理 |
| **并发控制** | `concurrency: block` 拒绝冲突任务（409） |
| **健康检查** | Skill 定义的 `health_check` 轮询 |

### P1 — 增强

| 能力 | 说明 |
|---|---|
| 作业队列 | `POST /v1/execute` 返回 `task_id`，异步执行，断网后重连查询 |
| 双执行器 | LocalExecutor (systemd-run) / DockerExecutor |
| 交互步骤 | `interactive: true` 暂停，API 返回 prompt 等用户输入 |
| 预检 | 执行前检查前提条件 |

### P2

| 能力 | 说明 |
|---|---|
| Skill 热加载 | Git 仓库同步，文件变更自动重载 |
| 资源限制 | systemd cgroup 限制 CPU/内存 |

---

## 五、API 设计

```
POST   /v1/execute
  Body: {"skill": "<yaml>", "params": {...}}
  → {"task_id": "abc123", "status": "running"}

GET    /v1/tasks/{task_id}
  → {"task_id": "abc123", "status": "running|completed|blocked|failed",
      "current_step": 2, "total_steps": 3,
      "steps": [{"id": "build", "status": "ok"}, ...],
      "error": null}

GET    /v1/tasks/{task_id}/log
  → [{"step": "build", "command": "...", "exit_code": 0,
      "output": "...", "duration_ms": 1234, "rolled_back": false}, ...]

POST   /v1/tasks/{task_id}/resume
  → {"status": "resumed"}       # 重试当前失败步骤

POST   /v1/tasks/{task_id}/skip
  → {"status": "resumed"}       # 跳过当前步骤继续后续

POST   /v1/tasks/{task_id}/abort
  → {"status": "aborted"}       # 终止 + 执行已完成的回滚

POST   /v1/tasks/{task_id}/interact
  Body: {"variable": "port", "value": "8080"}
  → {"status": "resumed"}

GET    /v1/health
  → {"status": "ok", "uptime": 12345, "active_tasks": 1}
```

### 错误上报格式

当 agent 无法自动处理异常时返回：

```json
{
  "status": "blocked",
  "failed_step": {
    "id": "deploy",
    "command": "docker run -d -p 8080:8080 --name user-api user-api:latest",
    "exit_code": 125,
    "output": "port 8080 already in use by container abc123"
  },
  "attempted": ["retry x2", "rollback failed: container not found"],
  "completed_steps": [
    {"id": "build", "status": "ok"},
    {"id": "stop-old", "status": "ok"}
  ],
  "suggestions": [
    "端口 8080 被容器 abc123 占用",
    "尝试: docker stop abc123 或选择其他端口"
  ]
}
```

Agent 拿到这个上下文后，用自己的 SSH 排查、决策、然后调用 `resume` / `skip` / `abort`。

---

## 六、部署

```bash
pip install parrot-agent
parrot-agent --port 9090                       # 开发
sudo systemctl enable --now parrot-agent       # 生产
ssh -L 9090:127.0.0.1:9090 user@host           # 远程访问
```

### 目录结构

```
~/.parrot/
├── agent.db       # SQLite
├── audit/         # JSON 日志 (7天 TTL)
├── skills/        # 本地 Skill 缓存
└── agent.pid
```

---

## 七、迭代路线

| 迭代 | 内容 |
|---|---|
| 1 | 本地执行 + 回滚 + 审计日志 |
| 2 | HTTP API + 作业队列 + resume/skip/abort + 结构化错误 |
| 3 | 双执行器 + 交互步骤 |

---

## 八、明确不做

- MCP 集成 — 由 parrot-mcp 负责
- SSH 降级执行 — 不部署 agent 则 Agent 直接读 Skill YAML + SSH
- 多 Agent 集群协调
- Agent 自身 Docker 化
